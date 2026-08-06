# Consuming the data

The stream gives you IDs and timestamps, not a product. This is the modelling layer
built on top of it: facts, OpenTelemetry spans, name enrichment, and a dashboard.
Setup lives in the [README](../README.md).

Raw events are hard to query directly. Every event arrives twice — once at `*_created`, once at
`*_completed` — often minutes apart, so any useful question ("how long did that job queue?") is a
self-join over a `dynamic` column. The Kusto module builds the join once, as a serving layer, and
ships it with `create_analytics = true` (the default).

Three tiers, in [`modules/azure-kusto/kql/analytics.kql`](../modules/azure-kusto/kql/analytics.kql):

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

## OpenTelemetry export

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

## Repo and owner names

Run this one first. The stream is identifiers only, so an unenriched dashboard can offer you
`repo 1324410793` as a filter and nothing better. One REST call per repo fixes it, and the response
carries `owner.login` too, so orgs come free.

> The three enrichment scripts below, and the dashboard build tooling, are catalogued in
> [`scripts/README.md`](../scripts/README.md).

```bash
KUSTO_CLUSTER=https://<cluster>.<region>.kusto.windows.net python3 scripts/ingest-repos.py
```

Populates `Repos`, which backs `RepoNames()` and every `Hierarchy*()` function. Incremental — it
only resolves ids it hasn't seen, so re-running is cheap (17 repos in ~2s) and it belongs on a
schedule, since repos are created continuously. See
[Running enrichment on a schedule](#running-enrichment-on-a-schedule).

Rows are **appended, never replaced**, and readers take the newest row per id. Repos get renamed and
transferred between orgs; keeping the history means a six-month-old run still resolves to the name it
had at the time instead of silently retconning. Deleted repos are recorded as unresolved with a
`repo/{id}` label rather than dropped, so their runs stay countable — a deleted repo 404s *forever*,
so without that record you'd retry it on every pass and still lose the history.

## Step facts

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
[`kql/enrichment.kql`](../modules/azure-kusto/kql/enrichment.kql).

Two rules the ingester encodes, both learned the hard way:

- **Never call the job API for skipped or cancelled jobs.** They return HTTP 200 with `steps: []`
  because they never ran. They were 43% of job events, so skipping them cuts API calls ~45% and drops
  the apparent miss rate from 44% to 1.6%.
- **`##[group]` markers are not steps.** A single `Set up job` step emits three groups; most steps
  emit none. Matching log groups to real step names succeeded only 58% of the time. Group data is
  still useful for CodeQL/Dependabot internals — `WorkflowGroupFacts()` — but it answers a different
  question.

## Log bodies

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

See **[docs/workflow-logs.md](workflow-logs.md)** for the measured numbers, the ID-based route
table, the reference architecture, and the reconciliation sweep you need to make it durable.

## Running enrichment on a schedule

Since the dashboard tables became clickable this stopped being cosmetic. A `repository_id` that
`Repos` has not resolved yet produces an empty URL, so that row renders as plain text while its
neighbours are links — the table looks inconsistent rather than obviously stale, and there is no
error anywhere to tell you why. There is no web URL that takes a numeric id, so guessing one is not
an option: `api.github.com/repositories/{id}` is a real API endpoint (it is what these scripts call)
but `github.com/repositories/{id}` is **not a route** and 404s even when you are signed in. The only
fix is resolving the id to `owner/name` before the dashboard needs it. Measured here on 2026-08-05,
drift reached 19 unresolved repos out of 889 — 98.5% of job rows still linked, which is exactly the
failure mode that goes unnoticed.

All three scripts are incremental, so the natural home for them is a scheduled workflow rather than
your laptop. [`examples/enrichment-workflow.yml`](../examples/enrichment-workflow.yml) is a working one:
hourly, no stored Azure secret, `ubuntu-slim` (which already carries the Azure CLI, the GitHub CLI
and Python, so nothing needs installing).

The identity it authenticates as is opt-in. Set `enrichment_subjects` to the workflows you want to
trust and the module creates a **second** managed identity for them:

```hcl
module "kusto_sink" {
  source = "git::https://github.com/austenstone/terraform-actions-data-stream.git//modules/azure-kusto?ref=v0.3.0"
  # ...
  enrichment_subjects = ["repo:my-org/observability:ref:refs/heads/main"]
}
```

```bash
terraform output enrichment_identity   # -> client_id, tenant_id for azure/login
```

Adding this to a deployment that is already running is additive — four resources, nothing
destroyed — but the plan may not look that way, because any `.kql` edit you have picked up since
your last apply forces a script replacement alongside it. See [Changing a materialized view needs a
manual drop](../modules/azure-kusto/README.md#changing-a-materialized-view-needs-a-manual-drop); `-target` the four
`*enrichment*` resources if you want just this.

It is deliberately a separate identity from the one the stream uses, and it holds **Viewer +
Ingestor**, not Admin. Enrichment has to *read* raw events to know which ids need resolving; the
stream never should. Splitting them means a compromised sink credential still cannot read your
history back out, and neither credential can drop a table — Terraform owns the schema, so nothing at
runtime needs the rights to change it.

Two things to get right in the workflow itself:

- **`GITHUB_TOKEN` is not enough.** It only sees the repo the workflow runs in, and enrichment
  resolves ids from every repo in the org. Use an org-scoped GitHub App installation token, or a
  fine-grained PAT limited to read-only Metadata and Actions.
- **Leave `ingest-logs.py` opt-in.** It is the only expensive one, and it — not the stream — is what
  will grow your database. The example puts it behind a `workflow_dispatch` input.

# Does this replace a CI observability product?

Mostly. Executions, failure rates, duration percentiles, per-step breakdowns, queue time, logs, and
distributed traces all have direct equivalents here. Two gaps: flame graphs have the data but no
native renderer (export the spans over OTLP), and **test-level results are absent entirely** — the
step is the finest granularity, so true flaky-test detection is not possible from this feed.

See **[docs/ci-observability-parity.md](ci-observability-parity.md)** for the panel-by-panel
comparison, measured against a live org.

# Dashboard

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
[`modules/azure-kusto/dashboard/RealTimeDashboard.json`](../modules/azure-kusto/dashboard/RealTimeDashboard.json)
with `${cluster_uri}` / `${database}` placeholders; Terraform renders it. Every tile query has been
executed against a live cluster, so an empty tile means no matching data, not a broken query.
Tables with an exact GitHub target deep-link readable repo, workflow, branch, run, and job values;
aggregate labels without one honest target deliberately remain plain text.

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
