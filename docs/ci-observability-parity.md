# Can this replace a CI observability product?

The usual version of this question is "we're paying for Datadog CI Visibility (or CircleCI Insights,
or Trunk, or Buildkite Analytics) — does the Actions Data Stream give us the same thing?"

Mostly yes, with one visualization gap and one hard data gap. Everything below is measured against a
live org, not asserted. Numbers are from a 24-hour window unless noted.

## Verdict

| What those products show you | Here | Evidence |
|---|---|---|
| Total pipeline executions | ✅ | 2,874 runs |
| Failure rate | ✅ | 330 failed, **11.5%** |
| Duration percentiles | ✅ | p50 41s, p95 275s |
| Slowest / most failure-prone pipelines | ✅ | ranked by `RunFacts()` |
| Per-job and per-step breakdown | ✅ | 47,785 executed steps across 531 distinct names |
| Step-level pass/fail rates | ✅ | `Dependency Review` 19.7%, `Run Dependabot` 5.4% |
| Queue time / runner contention | ✅ | p50 2s, p90 10s, p99 24s |
| Logs attached to a failing step | ✅ | 763,268 lines indexed |
| Distributed trace of a run | ✅ | 68,815 spans, 6,607 traces, 0 orphans |
| Re-run and attempt tracking | ✅ | after [#23](https://github.com/austenstone/terraform-actions-data-stream/issues/23) workaround |
| **Flame graph visualization** | ⚠️ | data yes, native rendering no |
| **Test-level results and flaky-test detection** | ❌ | not in the feed at all |

## The two gaps, honestly

### Flame graph — the data is there, the renderer isn't

A run's job and step timings form a complete, well-formed span tree: 68,815 spans across 6,607 traces
with zero orphaned parents. That is exactly what a flame graph draws. Kusto dashboards just have no
gantt or flame `visualType` to draw it with.

Two mitigations, neither of which is "buy a product":

- **`RunTimeline(run_id)`** renders the same information as a text gantt using unicode bars. It is
  less pretty and entirely sufficient for "which step ate the wall clock".
- **Export the spans over OTLP.** The trace tree is already validated and hash-compatible with the
  OpenTelemetry Collector's `githubreceiver`, so it drops into Grafana Tempo, Honeycomb, Jaeger, or
  Datadog APM — where you get a real flame graph. See the OpenTelemetry export section of the README.

If flame graphs are the whole reason you're paying, export the spans and keep the visualization
layer you like. You do not need a separate CI product to produce them.

### Test-level results — genuinely absent

The stream's finest granularity is the **step**. There is no test case, no assertion, no
pass/fail/skip per test, and therefore no true flaky-test detection, no test-suite duration trend,
and no "this test has failed 4 of the last 100 runs on main".

This is the one place a dedicated CI observability product does something this cannot, and it is not
a limitation you can engineer around from the feed alone — the data never leaves the runner.

What you can get instead:

- **Step-level failure rates**, which catch a flaky *step* even if not a flaky *test*. Real examples
  above: `Dependency Review` failing 19.7% of 488 executions is exactly the signal you'd want, and
  arrives without instrumenting anything.
- **Attempt tracking** as a coarse flake proxy: a run that succeeds on attempt 2 after failing
  attempt 1 is, by definition, flaky. `RunFacts()` exposes `attempt` per row, so
  `succeeded on attempt > 1` is a one-line query. Read
  [#23](https://github.com/austenstone/terraform-actions-data-stream/issues/23) first — re-runs emit
  no creation event, and keying on `run_id` without `attempt` produces wildly wrong durations.
- **Test reports uploaded as artifacts**, parsed separately. Outside this feed's scope, but the usual
  answer for teams who need per-test history.

## What you get here that you don't get there

Worth stating, because the comparison usually runs one direction only.

- **No agent, no instrumentation, no webhook receiver to operate.** The events are pushed to your
  sink. There is nothing to install in a workflow and nothing to keep running.
- **The data is yours, in your subscription.** Retention, cost, and access are your policy decisions.
  This deployment holds 365 days with recoverability enabled.
- **Raw event fidelity.** Nothing is pre-aggregated or sampled away, so a question nobody anticipated
  is still answerable a year later.
- **Cost.** A Dev-SKU cluster is roughly $0.14/hr. Per-seat or per-pipeline CI observability pricing
  is not in that neighborhood.

## Where it will genuinely disappoint you

Being straight about it, since these bite in the first week:

- **No billing or cost data.** No billable minutes, no runner SKU, no dollar figures. `job_labels[]`
  and `runner_group_name` are the only runner signal. This is not a chargeback feed.
- **IDs, not names.** Repos, actors, and owners arrive as numeric ids
  ([#21](https://github.com/austenstone/terraform-actions-data-stream/issues/21)). `ingest-repos.py`
  exists solely to resolve them, and it cannot recover names for deleted repos.
- **No DORA deployment metrics.** CI throughput and CI failure rate are computable. Deployment
  frequency, change failure rate, and time-to-restore are not — nothing here knows what a deployment is.
- **Several preview-stage data bugs.** All 23 are filed as issues on this repo. The two that will
  corrupt your numbers silently rather than loudly are
  [#22](https://github.com/austenstone/terraform-actions-data-stream/issues/22) (jobs that never
  reached a runner still report duration) and
  [#23](https://github.com/austenstone/terraform-actions-data-stream/issues/23) (re-run durations off
  by ~3,100x). Both are handled in the gold-layer functions here; hand-rolled queries will hit them.
