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

> [!TIP]
> Everything below was measured against a live stream rather than read off a docs page, and the
> rough edges found along the way have been reported to the service team.

## What gets created

| Module | Creates |
|---|---|
| [`modules/azure-identity`](modules/azure-identity) | User-assigned managed identity + federated credential. Used by the other Azure modules. |
| [`modules/azure-blob`](modules/azure-blob) | Storage account, container, `Storage Blob Data Contributor` role assignment. |
| [`modules/azure-kusto`](modules/azure-kusto) | Kusto cluster, database, table + ingestion mapping, `Ingestor` role assignment. |
| [`modules/azure-event-hub`](modules/azure-event-hub) | Event Hubs namespace, hub, `Azure Event Hubs Data Sender` role assignment. |
| [`modules/aws-s3`](modules/aws-s3) | OIDC provider, IAM role with a scoped trust policy, S3 bucket, write policy. |

Each module outputs a `sink_config` object you paste straight into the data stream configuration.

> **Only `azure-kusto` can be deployed by a plain Contributor.** Kusto grants data-plane access
> through a *Kusto principal assignment*, which Contributor can create. Blob and Event Hubs both need
> an Azure RBAC role assignment, which requires **User Access Administrator or Owner**. If you only
> have Contributor, set `create_role_assignment = false` and hand the `role_assignment_command`
> output to whoever does. Worth knowing before you book the meeting.

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

The subject is scoped to the **org**, not the sink. There is no `sink_id` in it, so one managed
identity (or one IAM role) serves every sink you create — you do not need a new federated credential
per destination. Deploy [`modules/azure-identity`](modules/azure-identity) once and reuse its
`client_id` across all of them.

Enterprise-scoped sinks exist at `/enterprises/{slug}/actions/data-stream/sinks` and use
`actions-data-stream:enterprise/<id>`, but they need enterprise-owner rights — org admin gets a 404.
If you want one, find your enterprise owner first.

## Quick start

The examples below clone the repo and use relative module paths. To consume a module from your own
Terraform instead, point `source` at the repo — no clone, and you can pin a version:

```hcl
module "sink" {
  source = "git::https://github.com/austenstone/terraform-actions-data-stream.git//modules/azure-kusto?ref=v0.2.0"

  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  github_owner_id     = "1234567"
  github_owner_type   = "organization"
  cluster_name        = "myadscluster"
}
```

Pin `ref` to a tag; `main` moves, and it moves in ways Terraform cannot resolve for you. The
analytics layer contains materialized views, and [a materialized view cannot be
altered](modules/azure-kusto/README.md#changing-a-materialized-view-needs-a-manual-drop) — changing one means dropping and
rebuilding it. Tracking `main` means a routine `terraform apply` can hand you that operation
unannounced. Swap the `//modules/...` path for `azure-blob`, `azure-event-hub` or `aws-s3` as
needed — every module exposes a `sink_config` output you paste into the data stream configuration.

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

### Azure Event Hubs

```bash
cd examples/azure-event-hub
terraform init
terraform apply
```

Standard SKU by default — Basic caps retention at one day and allows a single consumer group, which
is usually too tight for anything real. The hub's `partition_count` only buys consumer parallelism:
the data stream sends over the legacy Service Bus REST endpoint (`/messages?api-version=2014-01`)
and exposes no partition key, so events land round-robin. Combined with the documented lack of
ordering guarantees, **do not expect per-repo or per-run ordering out of Event Hubs**, even with a
single consumer. See [#12](https://github.com/austenstone/terraform-actions-data-stream/issues/12).

Budget extra time debugging this one: Event Hubs returns a bare **`401` with an empty body** for
both a missing `Azure Event Hubs Data Sender` role *and* a hub name that doesn't exist. Blob and
Kusto pass their destination's real error through (`AuthorizationPermissionMismatch`,
`BadRequest_EntityNotFound`); Event Hubs tells you nothing. Check the hub name before you go
hunting for a role assignment.

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

> [!NOTE]
> `deploy.yml` is linted and reviewed but has not been run end to end, because doing so requires
> rights on a subscription that the author does not have. Run `plan` first and read the output
> before trusting it with an `apply`.

### Bootstrapping the deploy identity

The workflow authenticates as an identity that has to exist before the workflow can run, which the
workflow therefore cannot create. Run this once from a shell with Owner or User Access Administrator:

```bash
scripts/bootstrap-deploy-identity.sh <subscription_id> <owner/repo>
```

It creates a managed identity, adds a federated credential per environment, grants Contributor, and
prints the three variables to set.

This is a **different** federated credential from the one the modules create. Keeping them straight
matters, because the failure mode is identical either way:

| | Subject | Lets |
|---|---|---|
| Created by `modules/azure-identity` | `actions-data-stream:organization/<id>` | GitHub's data stream service write to your sink |
| Created by the bootstrap script | `repo:<owner>/<repo>:environment:<name>` | A workflow in your repo deploy infrastructure |

### Configuration

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

## What running it actually taught me

The modules above are the easy half. These are written up separately because they're findings, not
setup — but read the operating one before you depend on the stream for anything.

- **[What the stream actually delivers](docs/findings.md)** — completeness reconciled against the
  REST API over 30 days, what's really in a payload, and why you probably don't need a reorder
  buffer. Short version: emission is solid, ~1% of runs never emit a `completed` event.
- **[Operating a sink](docs/operating.md)** — `sink_health` is failure-only and goes stale, a sink
  **never recovers on its own** from a destination outage (79% of events lost in a measured one),
  and the per-org cap is two sinks. The section that will actually page you.
- **[Consuming the data](docs/consuming.md)** — the Kusto serving layer, OpenTelemetry span export,
  turning IDs into repo and actor names, step facts, and the dashboard.

Rough edges are filed as
[`preview-bug`](https://github.com/austenstone/terraform-actions-data-stream/issues?q=is%3Aissue+label%3Apreview-bug)
issues; plans are in [#7](https://github.com/austenstone/terraform-actions-data-stream/issues/7).

## Cleanup

```bash
terraform destroy
```

Delete the sink in GitHub first, otherwise it will keep retrying against resources that no longer
exist.
