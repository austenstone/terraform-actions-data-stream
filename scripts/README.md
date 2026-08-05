# scripts/

Two unrelated groups live here. Only the first has anything to do with running
the pipeline.

## Enrichment — you run these on a schedule

The Data Stream carries identifiers and outcomes, not names or step detail.
These three fetch what it omits and land it beside the raw events. All of them
read `KUSTO_CLUSTER` and honour `KUSTO_TABLE` (default `ActionsEvents`),
authenticate with your `az login` identity, and are safe to re-run — each one
skips work it has already done.

| Script | Fills in | Backs |
|---|---|---|
| `ingest-repos.py` | `repository_id` → `owner/name` | `RepoNames()`, `Hierarchy*()`, every readable dashboard label |
| `ingest-steps.py` | per-step timings via the job API | `StepFacts()`, `OtelStepSpans()`, the Steps page |
| `ingest-logs.py` | log bodies for failed runs | `WorkflowLogs`, the Explorer page's log pane |

Cost scales very differently across the three. `ingest-repos.py` is seconds and
touches only ids it has never resolved. `ingest-logs.py` downloads and expands
zip archives, so scope it — it is the one that will surprise you.

## Dashboard — you run these only to change the dashboard

`modules/azure-kusto/dashboard/RealTimeDashboard.json` is committed as a
Terraform template and is the source of truth. You do not need anything below
to *deploy* it, only to *modify* it.

| Script | Does |
|---|---|
| `validate_dashboard.py` | Checks the JSON against the published schema. Run before every publish. |
| `publish-dashboard.py` | Renders the template and pushes it to an existing Fabric item. |
| `add-stream-health-page.py` | Regenerates the Stream health page |
| `add-explorer-page.py` | Regenerates the Explorer page |
| `add-live-page.py` | Regenerates the Live page |
| `add-dashboard-links.py` | Adds GitHub deep links to tables with exact repo, workflow, run, or job targets |
| `fix-multistat-height.py` | Repairs multistat tiles authored below the client's minimum height |
| `test-explorer-queries.py` | Runs the Explorer page's queries against a live cluster |

**Order matters.** Each page builder appends its tiles and shifts what follows,
so run them in the order listed and `add-live-page.py` last. Then
`add-dashboard-links.py`, `fix-multistat-height.py`, `validate_dashboard.py`,
and publish.

Validation is not optional here, and not because the write might fail. The
write succeeds either way. The Fabric API, `fab`, and Terraform all accept a
document the browser client will then refuse to load, and the client reports
that refusal as a version mismatch between two identical version numbers. The
schema check is the only thing in the chain that will tell you the truth.

`validate_dashboard.py` is vendored, not authored here. It is a copy of the
one in the `fabric-dashboards` agent skill, kept in-repo so this repository
stands alone with no private dependency. Two copies drift silently, so treat
the skill's copy as canonical and re-copy rather than editing this one in
place.
