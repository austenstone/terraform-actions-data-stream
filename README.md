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
> Everything below was measured against a live stream rather than read off a docs page. Findings
> are written up for GitHub field teams in
> [github/actions-sales#953](https://github.com/github/actions-sales/discussions/953) and reported
> to the service team in
> [github/actions-data-stream#211](https://github.com/github/actions-data-stream/issues/211).

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

## Known preview issues

Data Stream is a preview feature and has rough edges. The ones I've hit are logged as
[`preview-bug`](https://github.com/austenstone/terraform-actions-data-stream/issues?q=is%3Aissue+label%3Apreview-bug)
issues here — worth a skim before you debug something that isn't your fault. The two that cost the
most time:

- [#1](https://github.com/austenstone/terraform-actions-data-stream/issues/1) — Kusto sinks **500**
  instead of returning a validation error when `ingestion_uri` is missing, and it's required even for
  streaming ingestion.
- [#2](https://github.com/austenstone/terraform-actions-data-stream/issues/2) — a sink reports
  `active` while delivering nothing, because event emission is behind a *second* feature flag.
- [#11](https://github.com/austenstone/terraform-actions-data-stream/issues/11) — `sink_health`
  reports failures well, but `last_success_at` only tracks connection tests, so a busy sink and a
  silent one look identical — and an unhealthy sink doesn't self-heal. See
  [Operating it](#operating-it).
- [#12](https://github.com/austenstone/terraform-actions-data-stream/issues/12) — Event Hubs sends
  over the 2014-era Service Bus REST API with no partition key, so events land round-robin and
  per-run ordering is impossible.
- [#14](https://github.com/austenstone/terraform-actions-data-stream/issues/14) — **you get two
  sinks per org.** The third create returns `429 Too Many Requests` with an HTML body, so it reads
  as a rate limit and no amount of backoff clears it. Delete a sink and the next create succeeds
  instantly. Budget your two slots before you start.
- [#15](https://github.com/austenstone/terraform-actions-data-stream/issues/15) — **Test connection
  writes a real row into your destination**, as an undocumented sixth event type
  `test_connection`. Filter `eventType != "test_connection"` in any raw-table query.
- [#16](https://github.com/austenstone/terraform-actions-data-stream/issues/16) — **sink
  create/update/delete is not written to the audit log at all**, and sinks carry no
  `created_at`/`updated_at`/`created_by`. An org-wide egress path can be added, repointed, or
  removed with no attributable record anywhere. Worth knowing before a security review asks.
- [#17](https://github.com/austenstone/terraform-actions-data-stream/issues/17) — **every skipped
  job emits its `workflow_job_completed` with an earlier `eventTimestamp` than its own
  `workflow_job_created`**. 100% of skipped jobs, 0% of success/failure. The skew is only 10-50ms,
  but a causal filter like `completed >= created` silently drops every skipped job — about a third
  of all job rows.
- [#18](https://github.com/austenstone/terraform-actions-data-stream/issues/18) — **workflow runs on
  pull requests opened by coding agents are gated at `action_required` and never emit
  `workflow_run_completed`.** They're held for manual approval, so zero jobs dispatch — the exact
  path in [#8](https://github.com/austenstone/terraform-actions-data-stream/issues/8). The run is
  announced but never closes, so anything joining `created` → `completed` accumulates phantom open
  runs at ~1% of volume, permanently. This one grows with Copilot coding agent adoption. See
  [Is it complete?](#is-it-complete).
- [#19](https://github.com/austenstone/terraform-actions-data-stream/issues/19) — **a sink never
  recovers from a destination outage.** Break the destination for 11 minutes and the sink stops
  delivering *permanently*: 79% of events lost, loss continuing 13 minutes after the destination was
  healthy again, and no resumption until a human `PATCH`es the config. `status` reads `"active"` the
  entire time. This is the operational one to plan for — see [Operating it](#operating-it).

Plans and next steps are in
[#7](https://github.com/austenstone/terraform-actions-data-stream/issues/7).

### Is it complete?

Yes — with one scope note. This section is about **emission**: does GitHub announce every run? It
does. Whether those events survive the trip to your destination is a separate question, and the
answer there is worse — see
[#19](https://github.com/austenstone/terraform-actions-data-stream/issues/19) and
[Operating it](#operating-it).

Not one run is dropped. This is the question every enterprise asks first, and until now the
only answer was the stream agreeing with itself, which proves nothing.

Reconciled against the REST API by **set-comparing run IDs** — not counts — over 30 days and 4,333
run-completion events: **zero** runs reached the sink that the API didn't have, and every run the
API reported was announced on the stream. Nothing is lost and nothing is invented.

The real defect is narrower and lives in the *second half* of the pair. Roughly **1% of runs emit
`workflow_run_created` and then never emit `workflow_run_completed`**, so they stay permanently open
for any consumer that joins the two.

Orphans over a 7-day window, resolved against the REST API:

| conclusion | runs | why |
|---|---:|---|
| `action_required` | 33 | PR opened by a coding agent, held for approval — zero jobs dispatched |
| `failure` | 9 | workflow invalid before dispatch |
| `startup_failure` | 1 | workflow invalid before dispatch |
| `null` | 3 | legitimately still running |

43 genuine orphans out of 4,374 runs (**1.05%**), and **77% of them are the agent-PR approval
gate** — 24 authored by `Copilot`, 9 by `Claude`, not one of them a fork PR.

These runs never close. They are already terminal in the REST API the moment they're created
(`status: completed`, `created_at == updated_at`, zero jobs — 33 of 33), and approving the PR starts
a *new* run rather than resuming the gated one. There is no later event to wait for.

One root cause: a run that terminates without dispatching any job never closes out. Tracked as
[#8](https://github.com/austenstone/terraform-actions-data-stream/issues/8), with the approval-gate
trigger in [#18](https://github.com/austenstone/terraform-actions-data-stream/issues/18).

The gate is a repo setting, so this rate is tunable rather than fixed: **Settings → Copilot → Cloud
agent → "Actions workflow approval" → `Require approval for workflow runs`**, on by default
([docs](https://docs.github.com/en/enterprise-cloud@latest/copilot/how-tos/use-copilot-agents/cloud-agent/configuring-agent-settings#allowing-github-actions-workflows-to-run-automatically-when-copilot-pushes)).
Turning it off removes most of the orphans and accepts unreviewed agent-authored code reaching your
secrets — a real tradeoff, not a free win. Expect this 1% to *grow* as agent-opened PRs grow.

> Two cautions worth inheriting, both earned the hard way here.
>
> My first pass reported this as *2% of runs missing entirely*. It wasn't. `RunFacts()`
> inner-joins `created` → `completed`, so half-emitted runs vanished from my own view before I
> compared anything. **Always reconcile against the raw table, not a derived one** — and
> set-compare IDs, because five of six repos matched by count and the gap only ever surfaced on a
> set difference.
>
> My second pass called it the *fork-PR* approval gate. Also wrong — that was an inference from
> "approval gate" that I never tested. `POST /runs/{id}/approve` rejects all 33 with *"not from a
> fork pull request."* **The endpoint that would act on your theory is usually the cheapest way to
> falsify it.**

### Reconfiguring a live sink is safe

`PATCH`ing a sink — including **changing its destination** — is lossless and exactly-once. Measured
on a live stream: across a cutover from one Kusto table to another, a control sink saw 24 events and
the reconfigured sink's old and new tables saw 24 between them, with **zero dropped and zero
double-delivered**. The cutover minute splits cleanly across the two tables. An idempotent `PATCH`
(byte-identical config) is likewise a no-op on delivery.

Two caveats:

- A `PATCH` is validated by *actually delivering* to the new destination, so a bad config returns
  `422` with the destination's own error rather than persisting. That's good, but it means a
  brand-new Kusto table returns `422 BadRequest_EntityNotFound` for **~5 minutes** while the
  streaming schema cache warms. Create the table, wait, then repoint.
- A brand-new *sink* has a ~4 minute delivery warmup. Reconfiguring an existing one does not.

### You probably don't need a reorder buffer

The contract is at-least-once with **no ordering guarantee**, which reads as "assume total
disorder." Measured against the live stream, delivery is far better than that:

| pair | n | arrived out of causal order |
|---|---:|---:|
| `run_created` → `run_completed` | 617 | 2 (**0.32%**) |
| `job_created` → `job_completed` | 1,030 | 10 (**0.97%**) |

Measured with `ingestion_time()` at the destination, so it covers the full delivery path. About
**99% of pairs already arrive in causal order** and the exceptions are sub-second. Design for
out-of-order arrival — the contract allows it and you will see it — but a seconds-wide watermark
is enough. You do not need to buffer minutes.

The disorder you will actually hit comes from the *other* direction. 36% of jobs are emitted with
an inverted `eventTimestamp` ([#17](https://github.com/austenstone/terraform-actions-data-stream/issues/17)),
which is 37x more inversion than delivery introduces. Join on `job_id`/`workflow_run_id` and treat
`eventTimestamp` as approximate rather than as a causal ordering key.

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
  source = "git::https://github.com/austenstone/terraform-actions-data-stream.git//modules/azure-kusto?ref=v0.1.0"

  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  github_owner_id     = "1234567"
  github_owner_type   = "organization"
  cluster_name        = "myadscluster"
}
```

Pin `ref` to a tag; `main` moves, and it moves in ways Terraform cannot resolve for you. The
analytics layer contains materialized views, and [a materialized view cannot be
altered](#changing-a-materialized-view-needs-a-manual-drop) — changing one means dropping and
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

## Consuming the data

Raw events are hard to query directly. Every event arrives twice — once at `*_created`, once at
`*_completed` — often minutes apart, so any useful question ("how long did that job queue?") is a
self-join over a `dynamic` column. The Kusto module builds the join once, as a serving layer, and
ships it with `create_analytics = true` (the default).

Three tiers, in [`modules/azure-kusto/kql/analytics.kql`](modules/azure-kusto/kql/analytics.kql):

| Tier | Object | What it is |
|---|---|---|
| Bronze | `ActionsEvents` | Raw envelope, `eventData` as `dynamic`. Never query this directly. |
| Silver | `Jobs`, `Runs` | Materialized views. One row per job / per run, `created` and `completed` already paired. |
| Gold | `JobFacts()`, `RunFacts()` | Typed columns, computed durations, `runner_kind`, `job_name`. Start here. |
| — | `OtelSpans()` | The same data as OpenTelemetry `cicd.*` spans. See below. |

Materialized views rather than update policies: an update policy fires per-row at ingest and never
sees both halves of a pair, and [update policies using `join` conflict with streaming
ingestion](https://learn.microsoft.com/azure/data-explorer/kusto/management/update-policy).

```kusto
// Queue time by runner label — the question this feed is actually for
JobFacts()
| where runner_kind != "unknown"          // "unknown" == skipped jobs, all-zero timings
| summarize jobs = count(),
            p50 = percentile(queue_seconds, 50),
            p95 = percentile(queue_seconds, 95)
  by labels = tostring(job_labels), runner_group
| order by p95 desc
```

```kusto
// Orchestration overhead: wall time not explained by the longest job.
// Pure serialization and queue waste.
RunFacts()
| where jobs > 1
| summarize p95_overhead = percentile(wall_seconds - max_job_exec, 95), runs = count()
  by workflow_name
| order by p95_overhead desc
```

Four things to know before you build on it:

- **Filter `runner_kind == "unknown"`.** Skipped jobs emit both events with zero durations. On a real
  org they were 40% of rows and destroyed every average.
- **Key runs on `(run_id, attempt)`, never `run_id` alone.** A re-run reuses the run id and emits no
  `workflow_run_created` at all ([#23](https://github.com/austenstone/terraform-actions-data-stream/issues/23)),
  so pairing created→completed on the id splices a re-run's completion onto the original attempt's start.
  Measured: an 8-second re-run reported as 24,809 seconds. `RunFacts()` recovers the real start from the
  earliest `workflow_job_created` for the same attempt.
- **Dedupe on `eventUuid` if correctness matters.** Delivery is at-least-once. Across 20k observed
  events there were zero duplicates, but the contract permits them.
- **~5% of queue times compute negative** — `workflow_job_created` is occasionally emitted after the
  job actually starts. The gold functions leave these visible rather than clamping to zero, so filter
  `queue_seconds >= 0` in percentiles instead of hiding the skew.

### OpenTelemetry export

The stream carries `workflow_run_id`, `workflow_run_attempt`, and `check_run_id` — which happen to be
every hash input the [OpenTelemetry Collector's
`githubreceiver`](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/githubreceiver)
uses to build deterministic trace and span IDs. `OtelSpans()` reproduces them exactly, so the spans
are ID-identical to what a webhook-based receiver would emit:

```
SERVER     RUN Dependabot Updates    52043ms   parent=∅
INTERNAL     Dependabot              48000ms   parent=dacfe926…
INTERNAL       queue Dependabot       1389ms   parent=5c869269…
```

That means you can drop the **webhook receiver** — no public endpoint, no shared secret, no delivery
retries to babysit. Attributes follow the
[`cicd.*` semantic conventions](https://opentelemetry.io/docs/specs/semconv/cicd/cicd-spans/)
(currently Release Candidate, so expect churn).

Be clear about what this is, though: `OtelSpans()` is a **query**, not an exporter. It shapes the
data into spans; something still has to read them out of Kusto and push them to your tracing backend
— a scheduled job, a Grafana ADX datasource, or a collector with a Kusto receiver. The stream
replaces the ingest side of `githubreceiver`, not the egress side.

One data gap versus `githubreceiver` remains: no repo name or actor login (identifiers only). The
step-span gap is **closed** — see [Step facts](#step-facts) below, which produces 47k step spans
that parent onto the job spans with a 100% hit rate.

### Repo and owner names

Run this one first. The stream is identifiers only, so an unenriched dashboard can offer you
`repo 1324410793` as a filter and nothing better. One REST call per repo fixes it, and the response
carries `owner.login` too, so orgs come free.

> The three enrichment scripts below, and the dashboard build tooling, are catalogued in
> [`scripts/README.md`](scripts/README.md).

```bash
KUSTO_CLUSTER=https://<cluster>.<region>.kusto.windows.net python3 scripts/ingest-repos.py
```

Populates `Repos`, which backs `RepoNames()` and every `Hierarchy*()` function. Incremental — it
only resolves ids it hasn't seen, so re-running is cheap (17 repos in ~2s) and it's worth putting on
a schedule, since repos are created continuously.

Rows are **appended, never replaced**, and readers take the newest row per id. Repos get renamed and
transferred between orgs; keeping the history means a six-month-old run still resolves to the name it
had at the time instead of silently retconning. Deleted repos are recorded as unresolved with a
`repo/{id}` label rather than dropped, so their runs stay countable — a deleted repo 404s *forever*,
so without that record you'd retry it on every pass and still lose the history.

### Step facts

The stream carries no step data, but it does carry `check_run_id` — which, despite the name, **is the
public REST workflow-job id**. (`job_id` is internal and 404s on every route.) One call to
`GET /repositories/{id}/actions/jobs/{check_run_id}` returns exact step names, numbers, conclusions
and timings. No log parsing, ~1 KB per job.

```bash
KUSTO_CLUSTER=https://<cluster>.<region>.kusto.windows.net python3 scripts/ingest-steps.py
```

Both ingesters read `KUSTO_CLUSTER` (required), and optionally `KUSTO_DATABASE` and `KUSTO_TABLE`
— set the last two if you changed `database_name` or `table_name` from the module defaults. Auth
falls back to `az account get-access-token`, so an `az login` is enough; set `KUSTO_TOKEN` to
override.

Populates `WorkflowSteps`, which backs `StepFacts()`, `StepStats()`, `JobTimeSplit()`,
`StepFailures()`, `StepFailureHotspots()`, `OtelStepSpans()` and `OtelTraceTree()` from
[`kql/enrichment.kql`](modules/azure-kusto/kql/enrichment.kql).

Two rules the ingester encodes, both learned the hard way:

- **Never call the job API for skipped or cancelled jobs.** They return HTTP 200 with `steps: []`
  because they never ran. They were 43% of job events, so skipping them cuts API calls ~45% and drops
  the apparent miss rate from 44% to 1.6%.
- **`##[group]` markers are not steps.** A single `Set up job` step emits three groups; most steps
  emit none. Matching log groups to real step names succeeded only 58% of the time. Group data is
  still useful for CodeQL/Dependabot internals — `WorkflowGroupFacts()` — but it answers a different
  question.

### Log bodies

The stream carries **no log text, ever** — `eventData` is identifiers only. If you need the actual
run logs in Azure or Splunk, use the stream as an event-driven *trigger* and the REST API as the
*payload*: one rate-limit unit per run, and the archive download itself is unauthenticated and
unmetered.

```bash
KUSTO_CLUSTER=https://<cluster>.<region>.kusto.windows.net python3 scripts/ingest-logs.py
```

Be aware of the volume asymmetry: 148 runs produced 716k log lines and 165 MB in Kusto, and **a
single run was 83% of that**. Capacity planning on a mean will mislead you. Step facts are roughly a
thousandth of the storage for the more useful data, so reach for logs only when you genuinely need
the text.

See **[docs/workflow-logs.md](docs/workflow-logs.md)** for the measured numbers, the ID-based route
table, the reference architecture, and the reconciliation sweep you need to make it durable.

## Does this replace a CI observability product?

Mostly. Executions, failure rates, duration percentiles, per-step breakdowns, queue time, logs, and
distributed traces all have direct equivalents here. Two gaps: flame graphs have the data but no
native renderer (export the spans over OTLP), and **test-level results are absent entirely** — the
step is the finest granularity, so true flaky-test detection is not possible from this feed.

See **[docs/ci-observability-parity.md](docs/ci-observability-parity.md)** for the panel-by-panel
comparison, measured against a live org.

## Dashboard

The Kusto module ships an importable dashboard, already pointed at your cluster and database:

```bash
terraform output -raw dashboard_json > dashboard.json
```

Import it in [Kusto Web Explorer](https://dataexplorer.azure.com) (**Dashboards → New dashboard →
Import from file**), or into Fabric with `fab import`. Seven pages, forty-one tiles, built entirely
on the gold, hierarchy and step functions:

| Page | Answers |
|---|---|
| **Overview** | Is CI healthy right now? Throughput, outcomes, busiest workflows, what triggers them. |
| **Queue & runners** | Are we runner-constrained? Queue time by label, concurrency, slowest jobs to get a runner, runner-group utilization. |
| **Where time goes** | What is CI wasting? Failure hotspots, orchestration overhead, minutes lost queueing, longest jobs. |
| **Stream health** | Is the feed itself trustworthy? Ingestion lag percentiles, event mix, duplicate detection, delivery gaps. |
| **Steps** | Which *step* is slow or flaky? Time sinks with a p95/p50 spread column, runner overhead split, failure hotspots by wasted hours, the slow tail. Requires `ingest-steps.py`. |
| **Explorer** | Drill org → repo → workflow → job → step, then read the log lines of the step that failed. Requires `ingest-repos.py` for names. |
| **Live** | What is running *right now*? In-flight jobs and runs, queue depth, and how long the oldest thing has been waiting. |

Both filters — the time range and a workflow multi-select — are wired to every tile.

The source of truth is
[`modules/azure-kusto/dashboard/RealTimeDashboard.json`](modules/azure-kusto/dashboard/RealTimeDashboard.json)
with `${cluster_uri}` / `${database}` placeholders; Terraform renders it. Every tile query has been
executed against a live cluster, so an empty tile means no matching data, not a broken query.

> If you edit that JSON, **validate it against the published schema before importing**. Nothing in
> the write path checks it — the API, `fab`, and Terraform will all happily ship a document the
> browser client then refuses to load, and the client's only feedback is a single misleading
> sentence. Ask me how I know.

If your dashboard already lives in Fabric, `scripts/publish-dashboard.py` is the update path that
survives repeated edits. It renders the template, hard-fails if any `${…}` placeholder survived
substitution, and pushes via `updateDefinition` so the item id and its share links stay stable:

```bash
FABRIC_WORKSPACE=<workspace-guid> FABRIC_ITEM=<item-guid> \
  KUSTO_CLUSTER=https://<cluster>.<region>.kusto.windows.net \
  python3 scripts/publish-dashboard.py
```

The surviving-placeholder check is the point. An unsubstituted `${cluster_uri}` produces a dashboard
that imports cleanly and then fails to query anything, which looks identical to an empty cluster.

One thing the dashboard cannot show you: **cost**. The stream carries no billable minutes and no
runner SKU. Every duration is wall clock. `job_labels` and `runner_group_name` are the only runner
signal, which is enough to compare GHR-vs-SHR queue behaviour but not to price it.

## Operating it

Things that only show up once the stream has been running for a while.

### `sink_health` is failure-only

`GET /orgs/{org}/actions/data-stream/sinks` returns a `sink_health` block. It reacts to failures
well and to successes not at all.

**Failures are caught fast and reported verbatim.** I dropped the destination Kusto table out from
under a live sink; 56 seconds later:

```json
{
  "health_status": "unhealthy",
  "consecutive_failures": 2,
  "last_failure_at": "2026-08-04T23:22:54Z",
  "last_error": "upload failed on attempt 5 of 5: kusto upload failed with status 409: Conflict: ..."
}
```

The raw destination error is passed straight through, retries included. **Alerting on
`health_status != "healthy"` works** — use it.

**Successes are a different story.** There *is* a `last_success_at` field, but it only advances on
a connection test — never on a real delivery. Measured with two sinks on the same live traffic:

| sink | events delivered | `last_success_at` |
|---|---:|---|
| running 27h, never re-tested | 21,000+ | **absent from the response** |
| `PATCH`ed at `23:32:40Z` | 33 over the next 6 min | frozen at **`23:32:40Z`** |

So on a healthy sink these three are indistinguishable:

| Actual state | reported |
|---|---|
| delivering thousands of events/hour | `healthy` |
| delivering zero events, org is idle | `healthy` |
| delivering zero events because emission broke upstream | `healthy` |

That last row is the [#2](https://github.com/austenstone/terraform-actions-data-stream/issues/2)
failure mode, and health cannot see it — a stream that goes quiet never *fails* a delivery. So pair
the health check with a volume check at the destination:

```kusto
ActionsEvents | where eventTimestamp > ago(30m) | count   // alert if 0 during business hours
```

### A sink never recovers from a destination outage — and the loss is severe

This is the operational failure mode to plan for. Breaking a destination for 11 minutes cost **79%
of events, permanently**, and the bleeding continued for 13 minutes *after* the destination was
healthy again.

Controlled experiment, two sinks on identical live traffic, one destination dropped and repaired:

| Time (UTC) | Event |
|---|---|
| `04:40:07` | Destination table dropped. Outage begins. |
| `04:43:20` | First failed delivery. Health → `unhealthy`, verbatim Kusto error. **Detection is good.** |
| `04:43:20` → `05:05:19` | Health **never updates again**. `checked_at` and `consecutive_failures` frozen through ~180 more undeliverable events. |
| `04:51:40` | Table, mapping and streaming policy fully restored. **Destination is healthy.** |
| → `05:04:58` | **Loss continues for 13 more minutes.** The control sink delivers normally throughout. |
| `05:05:19` | Manual no-op `PATCH`. |
| `05:05:20` | Healthy, `consecutive_failures: 0`, delivery resumes and stays in sync. |

Set-comparing `eventUuid` against the control sink: **185 of 234 events lost (79%)**. `status` read
`"active"` the entire time — the sink list shows green while the sink is dead.

The retry budget is visible in the error text — `upload failed on attempt 1 of 5` — and there is no
durable buffer behind it. Batches still inside their five attempts when the destination recovered
got through; batches that had exhausted them were dropped and never retried. That's why the loss
window looks ragged rather than contiguous.

```bash
# the only recovery lever: touch the config
gh api -X PATCH /orgs/{org}/actions/data-stream/sinks/{id} --input sink.json
```

Catch: `PATCH` validates live, so **you cannot PATCH your way out until the destination is
independently verified working.** During the ADX schema-cache window below, the same call returned
`422`. Fix the destination, confirm it accepts writes, *then* PATCH. A `PATCH` also writes a
`test_connection` row into your table ([#15](https://github.com/austenstone/terraform-actions-data-stream/issues/15)).

**The damage is bounded, though.** Once the sink is revived it is genuinely fine — no lingering
corruption, no drift, no slow degradation. Set-comparing every event emitted after the `PATCH`
across both sinks: **82 in the control, 82 in the recovered sink, zero divergence in either
direction.** So your exposure is exactly:

```
outage duration  +  ~13 min of post-recovery bleed  +  time-to-notice
```

That last term is the only one you control, and it is the one that dominates. An outage caught in
five minutes is a rounding error; one caught the next morning is a hole in your dataset. This is
what makes the failure mode *manageable* rather than disqualifying — but only if you are watching
the destination.

**What to actually do about it.** Health tells you a sink broke, exactly once, and then goes stale —
so it is a trigger, not a monitor. Alert on both:

```kusto
// destination-side liveness — catches a dead sink even after health goes stale
ActionsEvents | where eventTimestamp > ago(30m) | count   // alert if 0 during business hours
```

and treat any `health_status != "healthy"` as **requiring a human to PATCH**, not as something that
will clear on its own. Budget for the fact that a destination blip costs materially more than the
blip itself, and that there is no backfill — events dropped during an outage are not retained
anywhere you can reach.

Tracked in [#19](https://github.com/austenstone/terraform-actions-data-stream/issues/19)
(data loss) and [#11](https://github.com/austenstone/terraform-actions-data-stream/issues/11)
(the health fields).

The good news: **you cannot save a broken sink.** `POST` and `PATCH` both run a real connection test
and reject on failure. Destination drift after creation is the only way a sink goes bad.

### Multiple sinks work, but new ones miss the first few minutes

Two sinks on the same org deliver the same events to both destinations. Measured over a seven-minute
window: 12 events, 12 in both, zero divergence. So "Kusto **and** S3" is a supported answer.

A freshly created sink does drop some events during registration — a second sink created at
`23:08:18Z` missed 2 of the 6 events in the following three minutes, then matched perfectly from
then on. Don't benchmark fan-out fidelity until a sink has been up for ~5 minutes.

The per-org sink cap is **two**. The third `POST` returns `429 Too Many Requests` — which reads as a
rate limit but is a quota; no amount of backoff clears it, and deleting a sink makes the next create
succeed immediately. See [#14](https://github.com/austenstone/terraform-actions-data-stream/issues/14).
Two slots is enough for "hot path + archive", not enough to add a non-prod destination alongside them.

Fan-out fidelity itself is exact. Over a 25-minute window with both sinks warm, sink A and sink B
received **304 of 304** identical events, and `eventUuid` is the *same value* in both — it identifies
the event, not the delivery. So a consumer merging two sinks can dedupe on it. The only rows that
differ between destinations are `test_connection` probes, which go only to the sink being tested
([#15](https://github.com/austenstone/terraform-actions-data-stream/issues/15)).

### ADX streaming ingestion caches table schema for ~5 minutes

If you add a table to an existing cluster and immediately point a sink at it, `test-connection`
fails:

```
kusto upload failed with status 400: BadRequest_EntityNotFound
```

even though `.show tables`, `.show table X ingestion json mappings` and
`.show table X policy streamingingestion` all confirm everything exists. This is ADX caching the
streaming schema, not a GitHub bug. Wait ~5 minutes after creating a table and retry. Terraform's
`azurerm_kusto_script` runs before the sink exists, so a clean `apply` is usually far enough ahead of
first ingest that you never see this.

### Changing a materialized view needs a manual drop

`azurerm_kusto_script` is idempotent, so editing a materialized view in
[`kql/analytics.kql`](modules/azure-kusto/kql/analytics.kql) and re-applying is a **no-op** — the
script has already run and `.create ifnotexists` does nothing. Functions are fine (they use
`.create-or-alter`), but views need:

```kusto
.drop materialized-view Jobs          // note: does NOT accept `ifexists`
.create materialized-view with (backfill=true) Jobs on table ActionsEvents { ... }
```

`backfill` is create-only. `.create-or-alter materialized-view with (backfill=true)` works exactly
once and then fails forever with *"Unsupported property in materialized view alter command"*.

## Cleanup

```bash
terraform destroy
```

Delete the sink in GitHub first, otherwise it will keep retrying against resources that no longer
exist.
