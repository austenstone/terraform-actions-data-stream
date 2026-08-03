# terraform-actions-data-stream

Terraform for the cloud-side resources that GitHub Actions Data Stream writes to.

Actions Data Stream pushes workflow and job telemetry from GitHub to a sink you own. It
authenticates with OIDC, so there are no keys or secrets to store anywhere. The tradeoff is that
the trust relationship has to be set up exactly right on your side before anything works. That
setup is what these modules do.

> [!NOTE]
> Actions Data Stream is in preview. Availability depends on your plan and feature enablement.

> [!IMPORTANT]
> **Enablement is two separate switches.** One turns on the settings page and the REST API, a
> second one turns on the actual event emission. It is entirely possible to create a sink, have
> it report `status: active` and `sink_health: healthy`, pass a connection test, and still receive
> zero workflow events — because only the first switch is on. If that's what you're seeing, your
> cloud config is fine; ask GitHub to enable event publishing for your org or enterprise.

## What gets created

| Module | Creates |
|---|---|
| [`modules/azure-identity`](modules/azure-identity) | User-assigned managed identity + federated credential. Used by the other Azure modules. |
| [`modules/azure-blob`](modules/azure-blob) | Storage account, container, `Storage Blob Data Contributor` role assignment. |
| [`modules/azure-kusto`](modules/azure-kusto) | Kusto cluster, database, table + ingestion mapping, `Ingestor` role assignment. |
| [`modules/aws-s3`](modules/aws-s3) | OIDC provider, IAM role with a scoped trust policy, S3 bucket, write policy. |

Each module outputs a `sink_config` object you paste straight into the data stream configuration.

## The part everyone gets wrong

The subject claim. It is:

```
actions-data-stream:organization/<numeric-org-id>
```

Two things trip people up:

1. It is **not** the same subject format as regular Actions OIDC (`repo:owner/name:ref`). A trust
   policy you already have for `actions/deploy` will not work here.
2. It is the **numeric** ID, not the slug. `actions-data-stream:organization/octodemo` is wrong.

Get yours from the data stream UI, or:

```bash
gh api /orgs/YOUR_ORG --jq .id
```

Enterprises use `actions-data-stream:enterprise/<numeric-enterprise-id>`.

The audience is `api://AzureADTokenExchange` on Azure and `sts.amazonaws.com` on AWS. The issuer is
`https://token.actions.githubusercontent.com` on both. All three are set for you by these modules.

## Quick start

### Azure Blob

Cheapest option and the fastest way to prove the OIDC chain works end to end.

```bash
cd examples/azure-blob
cp terraform.tfvars.example terraform.tfvars   # then edit it
terraform init
terraform apply
```

### Azure Data Explorer (Kusto)

```bash
cd examples/azure-kusto
terraform init
terraform apply
```

> [!WARNING]
> Even the `Dev(No SLA)` SKU bills roughly $100/month while the cluster is running. Destroy it when
> you are done, or set `auto_stop_enabled`.

The module creates the table and the JSON ingestion mapping for you, with columns matching the event
envelope:

| Column | Type |
|---|---|
| `eventUuid` | `string` |
| `eventType` | `string` |
| `eventTimestamp` | `datetime` |
| `enterpriseId` | `long` |
| `organizationId` | `long` |
| `eventData` | `dynamic` |

`eventData` is `dynamic` because its shape differs per event type.

### AWS S3

```bash
cd examples/aws-s3
terraform init
terraform apply
```

If your account already has the GitHub OIDC provider (likely, since regular Actions OIDC uses the
same one), set `create_oidc_provider = false`.

## Verify before you commit to it

Once `terraform apply` finishes, validate the trust relationship. This performs the real token
exchange against your identity provider and returns the raw error if anything is wrong. It creates
nothing.

```bash
terraform output -json sink_config \
  | jq '{name: "test", sink_type: "azure_blob", config: .}' \
  | gh api -X POST /orgs/YOUR_ORG/actions/data-stream/sinks/test-connection --input -
```

A `success: true` means GitHub can authenticate and write. Anything else comes back with the
provider's own error message, which is usually enough to identify the problem.

Or use the helper, which tests first and only creates the sink if the test passes:

```bash
scripts/create-sink.sh YOUR_ORG azure_blob my-sink examples/azure-blob
```

Common failures:

| Error contains | Cause |
|---|---|
| `AADSTS70021` | Subject claim mismatch. Check the numeric ID and the `organization` vs `enterprise` prefix. |
| `AADSTS700213` | Audience mismatch. Should be `api://AzureADTokenExchange`. |
| `403` / `AuthorizationPermissionMismatch` | Identity authenticated but lacks the data-plane role. Control-plane Contributor is not enough. |
| `Not authorized to perform sts:AssumeRoleWithWebIdentity` | AWS trust policy condition does not match the subject. |

Role assignments can take a minute or two to propagate. If auth succeeds but writes fail, wait and
retry before changing anything.

## Running it from GitHub Actions

There are two workflows. [`validate.yml`](.github/workflows/validate.yml) needs no configuration and
no credentials: it formats, validates every module and example, and runs weekly so provider releases
break CI instead of breaking you.

[`deploy.yml`](.github/workflows/deploy.yml) runs Terraform on dispatch, picking a target and one of
`plan`, `apply`, or `destroy`. It authenticates to the cloud with OIDC, so there is nothing to store.
That is the same trust pattern the data stream itself uses, which makes the workflow a working
reference for the thing you are configuring.

Set these as repository **variables**, not secrets. None of them are sensitive.

| Variable | Needed for | Notes |
|---|---|---|
| `GH_OWNER_ID` | all | Numeric org or enterprise ID. The subject claim is built from it. |
| `GH_OWNER_TYPE` | all | `organization` (default) or `enterprise`. |
| `AZURE_CLIENT_ID` `AZURE_TENANT_ID` `AZURE_SUBSCRIPTION_ID` | Azure | The identity the workflow deploys as. Needs its own federated credential for this repo. |
| `AZURE_LOCATION` | Azure | Defaults to `eastus`. |
| `TF_STATE_STORAGE_ACCOUNT` | Azure | Globally unique. Created on first run. |
| `KUSTO_CLUSTER_NAME` | azure-kusto | Globally unique. |
| `KUSTO_SKU` | azure-kusto | Dev SKUs run on spare capacity and can fail. `Dev(No SLA)_Standard_D11_v2` is a reliable fallback. |
| `STORAGE_ACCOUNT_NAME` | azure-blob | Globally unique, 3-24 lowercase alphanumeric. |
| `AWS_ROLE_ARN` `AWS_REGION` | aws-s3 | Role the workflow assumes. |
| `TF_STATE_BUCKET` `S3_BUCKET_NAME` | aws-s3 | Globally unique. |
| `GH_ORG` `SINK_NAME` | optional | Only if you want the workflow to register the sink too. |

The one optional secret is `DATA_STREAM_TOKEN`, used by the `create_sink` input to register the sink
after a successful apply. It needs `admin:org` or the
`write_organization_actions_data_stream_sinks` fine-grained permission. Leave it unset and the
workflow prints the sink config to the job summary for you to paste in.

Two things worth knowing before you dispatch an `apply`:

- **The job targets an environment named after the action.** Add required reviewers to `apply` and
  `destroy` and nobody can spend money by dispatching a workflow.
- **State is bootstrapped on first run** into a storage account or bucket you name. Without it,
  `apply` is a one-way door: a second run would try to recreate everything and `destroy` would have
  nothing to work from.

Realistically, most organizations will run these modules from their own pipeline rather than this
one. That is fine, and it is the reason the modules do not assume anything about how they are
invoked. The workflow exists so you can prove the whole path works before wiring it into somewhere
that matters.

## Events

Four event types are emitted today:

- `workflow_run_created`
- `workflow_run_completed`
- `workflow_job_created`
- `workflow_job_completed`

A fifth, `actions_resolved` (requested action name/version alongside the **resolved SHA**, the one
that would make supply-chain auditing possible), appears in the documentation but does not currently
fire. Don't build against it yet.

Delivery is at-least-once with no ordering guarantee, so deduplicate on `eventUuid` and sort on
`eventTimestamp` rather than assuming arrival order.

Payloads are newline-delimited JSON. Blob and S3 write one object per batch.

### What's actually in a payload

`workflow_job_completed`:

```json
{
  "job_id": 73743554030,
  "job_uuid": "09808b57-83b9-576e-a95a-060b3e01faa4",
  "job_key": "matrix._1",
  "job_status": "completed",
  "job_conclusion": "success",
  "job_started_at": "2026-08-03T20:40:23.0000000Z",
  "job_completed_at": "2026-08-03T20:40:25.0000000Z",
  "job_labels": ["ubuntu-latest"],
  "runner_id": 1001336145,
  "runner_name": "GitHub Actions 1001336145",
  "runner_group_id": 0,
  "runner_group_name": "GitHub Actions",
  "repository_id": 1322162471,
  "repository_owner_id": 1234567,
  "check_run_id": 91811564148,
  "check_suite_id": 83659228428,
  "workflow_run_id": 30851260703,
  "workflow_run_uuid": "ab8e8a98-f8c0-4133-91e8-8742b4687a20",
  "workflow_run_attempt": 1,
  "workflow_run_actor_id": 22425467,
  "workflow_run_head_sha": "f65ca40f300c530c834a70fd303776addb92505f"
}
```

`workflow_run_completed` adds a nested `workflow` object (`id`, `name`, `path`, `state`,
`present_in_default_branch`) plus `workflow_run_event`, `workflow_run_name`, `workflow_run_number`,
`workflow_run_head_branch`, and `workflow_file_path`.

Two things to plan around:

- **Identifiers only, no names.** You get `repository_id`, not `owner/repo`, and
  `workflow_run_actor_id`, not a login. Anything human-readable requires a join against the REST API,
  so budget for a lookup table if you're building dashboards.
- **No billing data.** There are no billable minutes, runner sizes, or cost fields. `job_labels` and
  `runner_group_name` are the only runner signal. This is a telemetry feed, not a billing feed.

## Cleanup

```bash
terraform destroy
```

Delete the sink in GitHub first, otherwise it will keep retrying against resources that no longer
exist.
