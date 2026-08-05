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
- [#18](https://github.com/austenstone/terraform-actions-data-stream/issues/18) — **runs that end in
  `action_required` never emit `workflow_run_completed`.** That's the fork-PR approval gate, and it's
  the common trigger for [#8](https://github.com/austenstone/terraform-actions-data-stream/issues/8).
  The run is announced but never closes, so anything joining `created` → `completed` accumulates
  phantom open runs at ~1% of volume. See [Is it complete?](#is-it-complete).

Plans and next steps are in
[#7](https://github.com/austenstone/terraform-actions-data-stream/issues/7).

### Is it complete?

Yes. Not one run is dropped. This is the question every enterprise asks first, and until now the
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
| `action_required` | 33 | fork-PR approval gate — zero jobs dispatched |
| `failure` | 9 | workflow invalid before dispatch |
| `startup_failure` | 1 | workflow invalid before dispatch |
| `null` | 3 | legitimately still running |

43 genuine orphans out of 4,374 runs (**1.05%**), and **77% of them are the approval gate**.

One root cause: a run that terminates without dispatching any job never closes out. Tracked as
[#8](https://github.com/austenstone/terraform-actions-data-stream/issues/8), with the approval-gate
trigger in [#18](https://github.com/austenstone/terraform-actions-data-stream/issues/18).

> A caution worth inheriting: my first pass reported this as *2% of runs missing entirely*. It
> wasn't. `RunFacts()` inner-joins `created` → `completed`, so half-emitted runs vanished from my
> own view before I compared anything. **Always reconcile against the raw table, not a derived
> one** — and set-compare IDs, because five of six repos matched by count and the gap only ever
> surfaced on a set difference.

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

Three things to know before you build on it:

- **Filter `runner_kind == "unknown"`.** Skipped jobs emit both events with zero durations. On a real
  org they were 40% of rows and destroyed every average.
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

Two data gaps versus `githubreceiver`: no step-level spans (the stream carries no step data), and no
repo name or actor login (identifiers only).

## Dashboard

The Kusto module ships an importable dashboard, already pointed at your cluster and database:

```bash
terraform output -raw dashboard_json > dashboard.json
```

Import it in [Kusto Web Explorer](https://dataexplorer.azure.com) (**Dashboards → New dashboard →
Import from file**), or into Fabric with `fab import`. Four pages, twenty tiles, built entirely on
the gold functions:

| Page | Answers |
|---|---|
| **Overview** | Is CI healthy right now? Throughput, outcomes, busiest workflows, what triggers them. |
| **Queue & runners** | Are we runner-constrained? Queue time by label, concurrency, slowest jobs to get a runner, runner-group utilization. |
| **Where time goes** | What is CI wasting? Failure hotspots, orchestration overhead, minutes lost queueing, longest jobs. |
| **Stream health** | Is the feed itself trustworthy? Ingestion lag percentiles, event mix, duplicate detection, delivery gaps. |

Both filters — the time range and a workflow multi-select — are wired to every tile.

The source of truth is
[`modules/azure-kusto/dashboard/RealTimeDashboard.json`](modules/azure-kusto/dashboard/RealTimeDashboard.json)
with `${cluster_uri}` / `${database}` placeholders; Terraform renders it. Every tile query has been
executed against a live cluster, so an empty tile means no matching data, not a broken query.

> If you edit that JSON, **validate it against the published schema before importing**. Nothing in
> the write path checks it — the API, `fab`, and Terraform will all happily ship a document the
> browser client then refuses to load, and the client's only feedback is a single misleading
> sentence. Ask me how I know.

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

### An unhealthy sink does not recover on its own

Same experiment, continued. After the table was **recreated** and the destination was healthy again,
the sink stayed `unhealthy` for the full 8 minutes I watched it, delivering nothing —
`consecutive_failures` frozen at 2, `checked_at` frozen. It came back only when I manually `PATCH`ed
it with an identical config, and then delivery resumed immediately.

```bash
# recovery lever after a destination outage
gh api -X PATCH /orgs/{org}/actions/data-stream/sinks/{id} --input sink.json
```

Catch: `PATCH` validates live, so **you cannot PATCH your way out until the destination is
independently verified working.** During the ADX schema-cache window below, the same call returned
`422`. Fix the destination, confirm it accepts writes, *then* PATCH.

Both of these are tracked in
[#11](https://github.com/austenstone/terraform-actions-data-stream/issues/11).

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
