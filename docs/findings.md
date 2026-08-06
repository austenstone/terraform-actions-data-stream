# What the stream actually delivers

Measured against a live org over 30 days, reconciled against the REST API rather than
taken on faith from the stream agreeing with itself. Setup lives in the
[README](../README.md); this is what you get once it's running.

## Can you trust it?

Mostly yes, with specific exceptions — all of them reconciled against the REST API rather than
taken on faith from the stream agreeing with itself.

Data Stream is a preview and has rough edges. The ones I've hit are filed as
[`preview-bug`](https://github.com/austenstone/terraform-actions-data-stream/issues?q=is%3Aissue+label%3Apreview-bug)
issues — worth a skim before you spend an afternoon debugging something that isn't your fault. The
ones that affect how you *operate* a sink are called out in context below and in
[Operating it](operating.md).

Plans and next steps are in
[#7](https://github.com/austenstone/terraform-actions-data-stream/issues/7).

## Is it complete?

Yes — with one scope note. This section is about **emission**: does GitHub announce every run? It
does. Whether those events survive the trip to your destination is a separate question, and the
answer there is worse — see
[#19](https://github.com/austenstone/terraform-actions-data-stream/issues/19) and
[Operating it](operating.md).

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

## Reconfiguring a live sink is safe

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

## You probably don't need a reorder buffer

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

# Events

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

## What's actually in a payload

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
