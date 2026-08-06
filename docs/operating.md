# Operating a sink

What breaks in production, how you find out, and what you have to do by hand.
Read this before you depend on the stream for anything. Setup lives in the
[README](../README.md).

Things that only show up once the stream has been running for a while.

## `sink_health` is failure-only

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

## A sink never recovers from a destination outage — and the loss is severe

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

## Multiple sinks work, but new ones miss the first few minutes

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
