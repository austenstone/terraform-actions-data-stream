# Getting workflow **logs** into Azure (or Splunk)

The Data Stream does **not** carry log text. It never will — `eventData` is IDs only.
This doc is the paved path for the adjacent question: *"how do I get the actual run logs
out of GitHub Actions and into my observability stack?"*

Everything below was measured against a live org, not inferred. Numbers are reproducible.

---

## TL;DR

**Use the Data Stream as the trigger, the REST API as the payload.**

```
workflow_run_completed  ──►  fetch that one run's logs  ──►  parse  ──►  your sink
     (event-driven)              (1 API call)              (OTel)
```

No polling. No public webhook endpoint. One API call per workflow run.

---

## First: the rate limit is almost certainly not your problem

This is the objection that kills most log-ingestion projects before they start, and it's
based on a per-*job* or per-*poll* mental model. Measured reality:

| Fact | Measured |
|---|---|
| `GET /repos/{o}/{r}/actions/runs/{id}/logs` cost | **1 rate-limit unit** (`x-ratelimit-remaining` 14945 → 14944) |
| What you get for it | **Every job in the run**, zipped, in one response |
| The actual download | **302 → pre-signed URL** on `results-receiver.actions.githubusercontent.com` |
| Cost of that download | **Zero rate limit, no `Authorization` header required** (verified: bare `curl` → `200`) |
| Budget, GitHub App installation on a GHEC org | **15,000 requests/hour** ([docs](https://docs.github.com/en/enterprise-cloud@latest/rest/using-the-rest-api/rate-limits-for-the-rest-api#primary-rate-limit-for-github-app-installations)) |

So the ceiling is **15,000 runs/hour ≈ 360,000 runs/day, per installation.**

For scale: a deliberately busy demo org measured over 24h did **2,702 completed runs/day**
— **0.75%** of the hourly budget. You would need ~133× that volume, in a single org, to
saturate it. And the limit is *per installation*, so multiple orgs multiply it.

> **The thing to actually avoid is polling**, not the API. Polling costs N calls per run per
> interval and scales with your *workflow count*. Event-driven costs 1 call per run and scales
> with your *completed runs*. Same endpoint, completely different bill.

---

## Why the Data Stream is a better trigger than a webhook

A `workflow_run` webhook works, but it makes you run and secure a public HTTPS endpoint:
ingress, TLS, HMAC validation, and an availability SLO — because a webhook you fail to
answer is a webhook you lose.

The Data Stream inverts that. GitHub pushes into **your** Event Hubs / Blob / Kusto over
OIDC, and your consumer *pulls*. No public ingress, no inbound firewall rule, no shared
secret, and the consumer can scale to zero.

**Timing (measured, n=2, single org):**

| Moment | Offset from run completion |
|---|---|
| Data Stream event emitted | **+0.7s to +1.1s** |
| Log archive fetchable via API | **+1s** (first poll — may be earlier) |
| Event delivered and queryable in the sink | **+4.1s to +7.3s** |

By the time your consumer sees the event, the logs have been ready for several seconds.
There is no race in the common case — but a 50-job run finalizes its archive more slowly
than a 4-job one, so **retry with backoff on a `404`** rather than assuming availability.

---

## The archive format

One call returns a zip containing, per job:

```
{index}_{jobName}.txt        full job log
{jobName}/system.txt         runner system log
```

Log lines are `RFC3339 nano timestamp` + space + message, with a BOM at file start:

```
2026-08-05T14:55:19.4711234Z ##[group]Run echo hello
2026-08-05T14:55:19.4823901Z hello
2026-08-05T14:55:19.4901188Z ##[endgroup]
```

- `##[group]` / `##[endgroup]` delimit **steps** — this is how you get step-level granularity
- `##[error]` / `##[warning]` / `##[notice]` carry **severity**
- Compression measured at ~2.4:1 on small runs

That maps onto OTel with no cleverness required:

| Log line part | OTel `LogRecord` field |
|---|---|
| leading timestamp | `Timestamp` |
| `##[error]` / `##[warning]` / `##[notice]` | `SeverityNumber` / `SeverityText` |
| remainder of the line | `Body` |
| enclosing `##[group]` | `cicd.pipeline.task.name` |

---

## ⚠️ Two gotchas that will silently corrupt your archive

**1. Partially re-run workflows.** Per the
[docs](https://docs.github.com/en/enterprise-cloud@latest/actions/monitoring-and-troubleshooting-workflows/using-workflow-run-logs):
*"When you download the log archive for a workflow that was partially re-run, the archive only
includes the jobs that were re-run."* If you only ever fetch the latest archive you will lose the
original attempt's jobs. Fetch **per attempt**: `GET .../runs/{id}/attempts/{n}/logs`.

**2. Retention is the whole reason to do this.** GitHub **deletes** logs after the retention
window — 90 days by default, configurable 1–90 (public) or 1–400 (private/internal). If you
want history beyond that, copying them out isn't an optimization, it's the only option.

---

## 🔴 Trigger-only is not durable. You need a reconciliation sweep.

This is the part most designs get wrong. The Data Stream's two clauses are different:

> **Emission** is solid. Every run is announced, nothing is invented.
> **Delivery** is best-effort — at-least-once *while the sink is healthy*.

Three measured failure modes mean a trigger-only pipeline **silently drops logs**:

| # | Failure | Impact |
|---|---|---|
| [#8](https://github.com/austenstone/terraform-actions-data-stream/issues/8) | Zero-job runs emit no `workflow_run_completed` | Small, but real |
| [#18](https://github.com/austenstone/terraform-actions-data-stream/issues/18) | ~**1%** of runs never emit a completion event (approval gate on agent-authored PRs) | Growing — agent-authored PRs are increasing |
| [#19](https://github.com/austenstone/terraform-actions-data-stream/issues/19) | A destination outage causes **permanent, unrecoverable loss**. 79% measured. No backfill. `status` still reads `"active"`. | Unbounded |

If your requirement is *"I don't want logs dropped"*, the trigger alone does not meet it.

**Add a low-frequency sweep.** Hourly, list runs completed in the last N hours and `leftanti`
them against what you actually archived; fetch the difference.

```
hot path:   Data Stream event  ──►  fetch logs   (seconds, ~1 call/run)
safety net: hourly sweep       ──►  fetch misses (a few hundred calls/hr)
```

The sweep is what turns best-effort delivery into an actual guarantee, and it costs a rounding
error against 15,000/hr. This is the same "hot path + archive" split the README already
recommends for events.

---

## Reference architecture

```
GitHub Actions
   │
   │  Data Stream (workflow_run_completed) — OIDC, no inbound ingress
   ▼
Azure Event Hubs  ◄── notification only (IDs), never log bodies
   │
   ▼
Container Apps Job  (KEDA-scaled, scale-to-zero, no platform timeout cap)
   ├─ 1. GET /runs/{id}/attempts/{n}/logs        → 1 rate-limit unit
   ├─ 2. unzip; split on ##[group]; parse severity + timestamps
   ├─ 3. emit OTLP LogRecords (trace_id/span_id from run_id + attempt + check_run_id)
   └─ 4. PUT raw .zip → Blob (Cool → lifecycle → Archive)   ← beats the 90d deletion
   │
   ▼
OTel Collector (Container App)
   ├─ azuremonitor        → Log Analytics  (OTelLogs / custom _CL table)
   ├─ splunk_hec          → Splunk HEC
   └─ azuredataexplorer   → ADX / Fabric Eventhouse
```

**Why Container Apps Jobs over Functions:** Azure Functions on the Consumption plan cap at a
10-minute timeout, which is not enough for reliable large-archive processing. Container Apps
Jobs have a configurable `replicaTimeout` with no platform maximum, and still scale to zero.

**Why Event Hubs is notification-only:** the Standard tier caps messages at 1 MB. That's fine
for an ID-only Data Stream event and completely wrong for a 5 MB log archive. Never put log
bodies on the bus — pass the `run_id` and let the job fetch.

### Picking the Azure sink

| Sink | When |
|---|---|
| **Log Analytics, Basic plan** custom table | Default. Cheapest queryable KQL surface. 30-day interactive window. |
| **Native OTLP → `OTelLogs`** | GA via the Collector's `otlphttp/azuremonitor` exporter + `azure_auth` extension (Collector ≥ 0.132.0, `Monitoring Metrics Publisher` on the DCR). Purest OTel story, but lands on the Analytics plan — you pay for the semantics. |
| **ADX / Fabric Eventhouse** | Only if already deployed. Cluster cost is disproportionate otherwise. |
| **Blob, Cool → Archive** | Always, in parallel. This is the retention answer. |

Verify current pricing at
[azure.microsoft.com/pricing/details/monitor](https://azure.microsoft.com/en-us/pricing/details/monitor/)
before quoting anything.

### Splunk

Two paths, both fine:
- Add the `splunk_hec` exporter to the same Collector — no extra code.
- Point the **Splunk Add-on for Microsoft Cloud Services** at the Event Hubs / Blob output.

---

## Correlating logs to traces — "one big trace"

The Data Stream carries `workflow_run_id` + `workflow_run_attempt` + `check_run_id`, which are
**exactly the hash inputs** OpenTelemetry's `githubreceiver` uses to derive span IDs. This repo's
[`OtelSpans()`](../modules/azure-kusto/kql/analytics.kql) function already emits ID-identical
spans from the stream alone.

So if you derive each log line's `TraceId`/`SpanId` from the same inputs, **logs and spans join
without a lookup table.** Run span → job spans → log lines, one trace.

Attribute with the OTel
[CI/CD semantic conventions](https://opentelemetry.io/docs/specs/semconv/cicd/cicd-spans/)
(status: **Release Candidate** — stable-ish, but not frozen):

```
cicd.pipeline.name          = workflow name
cicd.pipeline.run.id        = workflow_run_id
cicd.pipeline.run.url.full  = run html_url
cicd.pipeline.result        = conclusion
cicd.pipeline.task.name     = job name  (or ##[group] title for step granularity)
cicd.pipeline.task.run.id   = job_id
cicd.worker.name            = runner label
```

⚠️ The stream is **IDs only** — no repo name, no actor login, no workflow name outside
`workflow_run_*` events. You will need a cached lookup to make any of it human-readable.

---

## What about capturing from the runner instead?

[`austenstone/runner-internal-logs-action`](https://github.com/austenstone/runner-internal-logs-action)
grabs the runner's own `_diag/Runner_*.log` and `Worker_*.log` (Linux/macOS **and** Windows via
pwsh) and uploads them as an artifact. Pair it with `always()`.

Honest assessment: it's a **superset** of step output plus a lot of diagnostic noise, it's
per-job rather than per-run, and it is **not officially supported**. It's the right tool for
debugging a runner, and the wrong tool for a log pipeline. Use the API for the pipeline.
