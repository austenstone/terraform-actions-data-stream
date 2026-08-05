#!/usr/bin/env python3
"""Add (or replace) the "Live" page in the Kusto dashboard.

Every other page is a post-hoc view of completed work. This one answers "what is
happening right now", which the stream supports well -- ingestion lag is p50 5s /
p95 7s -- with one large caveat baked into every tile: there is no "job started"
event, so a job in flight is "queued OR executing" and the two cannot be told
apart. See InFlightJobs() in kql/analytics.kql.

Run this LAST in the builder chain. It rewrites the global date-range parameter's
showOnPages so the picker is hidden here, and that needs every other page to
already exist. Order: add-stream-health-page -> add-explorer-page ->
add-live-page -> publish-dashboard.
"""

import json
import pathlib
import re
import uuid

DASH = pathlib.Path(__file__).resolve().parent.parent / "modules/azure-kusto/dashboard/RealTimeDashboard.json"
NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
PAGE = "Live"

# Seconds -> a string a human reads at a glance. Kusto's format_timespan always
# renders all fields ("00:00:14"), which buries the magnitude that matters most
# on this page: the difference between 14 seconds and 14 hours.
HUMAN = """case(age_seconds < 90, strcat(tostring(age_seconds), "s"),
              age_seconds < 5400, strcat(tostring(round(age_seconds / 60.0, 1)), "m"),
              strcat(tostring(round(age_seconds / 3600.0, 1)), "h"))"""

SCORECARD = """let _j = materialize(LiveJobs());
let _live_runs = toscalar(LiveRuns() | where not(phantom) | count);
_j
| summarize jobs = count(),
            starved = countif(not(label_served)),
            repos = dcount(repo, 4),
            age_seconds = max(age_seconds)
| extend age_seconds = coalesce(age_seconds, 0)
| extend longest = """ + HUMAN + """
| extend Rows = pack_array(
    pack_array("Jobs in flight", tostring(jobs)),
    pack_array("Starved", tostring(starved)),
    pack_array("Runs in flight", tostring(_live_runs)),
    pack_array("Repos active", tostring(repos)),
    pack_array("Longest wait", iff(jobs == 0, "--", longest)))
| mv-expand Rows
| project title = tostring(Rows[0]), value = tostring(Rows[1])"""

JOBS = """LiveJobs()
| extend waiting = """ + HUMAN + """
// Not "queued vs running" -- the stream cannot distinguish those. The only
// honest split is whether anything with these labels has finished on a real
// runner in the last 24h.
| extend state = iff(label_served, "in flight", "STARVED: no runner serves these labels")
| project repo, workflow, job = job_name, labels, state, waiting, age_seconds,
          branch, trigger, run_id
| order by age_seconds desc"""

# The starvation diagnostic, aggregated. A label with a rising `waiting` count
# and `served == false` is a runner pool that is scaled to zero, retired, or
# offline -- the single most actionable thing on this page.
LABELS = """LiveJobs()
| summarize waiting = count(),
            repos = dcount(repo, 4),
            oldest_seconds = max(age_seconds),
            served = take_any(label_served)
  by labels
| extend verdict = iff(served,
        "pool is answering",
        "NO RUNNER: nothing with these labels has completed in 24h")
| project labels, waiting, repos, oldest_seconds, verdict
| order by oldest_seconds desc"""

# Deliberately not filtered to `queued` on the created side: the gap between the
# two lines is the backlog forming or draining, and dropping skipped jobs from
# one line only would make the two incomparable.
PULSE = """ActionsEvents
| where eventTimestamp > ago(2h)
| where eventType in ("workflow_job_created", "workflow_job_completed")
| summarize queued = countif(eventType == "workflow_job_created"),
            completed = countif(eventType == "workflow_job_completed")
  by timestamp = bin(eventTimestamp, 1m)
| order by timestamp asc"""

RUNS = """LiveRuns()
| where not(phantom)
| extend waiting = """ + HUMAN + """
| project repo, workflow, branch, trigger, live_jobs, starved_jobs,
          waiting, age_seconds, started_at, run_id
| order by age_seconds desc"""

EXPLAINER = """## What "live" means here, precisely

**The date range picker does not apply to this page.** Everything below is now.
Turn on auto-refresh (top right) to watch it move -- ingestion lag is p50 5s /
p95 7s, so this is genuinely near-real-time rather than a slow-moving report.

**There is no "job started" event.** The stream emits `workflow_job_created`
then `workflow_job_completed` and nothing in between, and `runner_name` is empty
on every created event (0 of 1,693 measured over 6h). So a job here is *queued
or executing* and nothing can tell you which, nor which runner picked it up.
Age is time-since-queued, never runtime.
[issue #21](https://github.com/austenstone/terraform-actions-data-stream/issues/21)

**So don't read age as "stuck".** A 40-minute job might be compiling or might be
waiting on a runner that will never come. `state` answers that instead: it asks
whether *any* job carrying the same labels has completed on a real runner in the
last 24 hours. If none has, the pool is dead -- a retired `ubuntu-20.04`, an ARC
deployment scaled to zero, a self-hosted box that never came back. Measured
live, this separated the population perfectly: every job open over 30 minutes
was starved, every job under 2 minutes was not.

**Waiting by label is the tile to alert on.** A label whose `waiting` count
climbs while `verdict` says NO RUNNER is a capacity outage in progress, and it
is visible here minutes before anyone opens a support ticket.

**Runs in flight hides phantoms.** A run that emitted `created`, never emitted
`completed`, and has no in-flight jobs is not running -- it either lost its
completion event or died before dispatching anything. Those are filtered out
here and diagnosed properly on Stream health.
[issue #8](https://github.com/austenstone/terraform-actions-data-stream/issues/8)"""

QUERIES = [
    # multistat has its own client minimum of 6 wide x 9 tall, unlike the 12 x 6
    # that applies to tables and charts. Anything shorter renders an error box.
    ("Right now", "In-flight work across the organization", "multistat", SCORECARD, 12, 9),
    ("Job arrival vs completion", "Jobs queued and completed per minute, last 2h", "line", PULSE, 12, 9),
    ("Jobs in flight", "Queued or executing, oldest first", "table", JOBS, 24, 8),
    ("Waiting by label", "Which runner pools are answering", "table", LABELS, 12, 7),
    ("Runs in flight", "Phantom runs excluded", "table", RUNS, 12, 7),
]

VISUAL_OPTIONS = {
    "multistat": {
        "multiStat__valueColumn": "value",
        "multiStat__labelColumn": "title",
        "multiStat__displayOrientation": "horizontal",
        # Slot grid must fit the tile. 5-across only works on a 24-wide tile;
        # this one is 12 wide, so use the 2x3 grid (6 cells, 5 filled).
        "multiStat__slot": {"width": 2, "height": 3},
        "multiStat__textSize": "auto",
        "colorRules": [],
        "colorRulesDisabled": True,
        "colorStyle": "light",
    },
    "line": {
        "xColumn": "timestamp",
        "yColumns": ["queued", "completed"],
        "seriesColumns": None,
        "hideLegend": False,
        "legendLocation": "bottom",
        "multipleYAxes": {"base": {"id": "-1", "columns": [], "label": "", "yAxisMaximumValue": None, "yAxisMinimumValue": None, "yAxisScale": "linear", "horizontalLines": []}, "additional": [], "showMultiplePanels": False},
        "xColumnTitle": "",
        "xAxisScale": "linear",
        "verticalLine": "",
        "selectedDataOnLoad": {"all": False, "limit": 10},
        "dataPointsTooltip": {"all": False, "limit": 10},
        "crossFilterDisabled": False,
        "drillthroughDisabled": False,
        "crossFilter": [],
        "drillthrough": [],
    },
    "table": {
        "colorRulesDisabled": True,
        "colorRules": [],
        "table__enableRenderLinks": True,
        "table__renderLinks": [],
        "crossFilterDisabled": False,
        "drillthroughDisabled": False,
        "crossFilter": [],
        "drillthrough": [],
    },
}


def main() -> None:
    dash = json.loads(DASH.read_text())
    page_id = str(uuid.uuid5(NS, f"page:{PAGE}"))

    dash["pages"] = [p for p in dash["pages"] if p["name"] != PAGE] + [{"name": PAGE, "id": page_id}]
    # Re-running this script drops the page it owns and any query only that page used.
    # A parameter's dropdown query is referenced from parameters[], never from a tile,
    # so a tile-only keep-set garbage-collects it and the client then rejects the whole
    # dashboard with "Query id <uuid> in parameter not found".
    keep = {t["queryRef"]["queryId"] for t in dash["tiles"] if t["pageId"] != page_id and "queryRef" in t}
    keep |= {
        qid
        for p in dash.get("parameters", [])
        if (qid := ((p.get("dataSource") or {}).get("queryRef") or {}).get("queryId"))
    }
    dash["tiles"] = [t for t in dash["tiles"] if t["pageId"] != page_id]
    dash["queries"] = [q for q in dash["queries"] if q["id"] in keep]

    source_id = dash["dataSources"][0]["id"]
    known = {p["variableName"] for p in dash["parameters"] if p.get("variableName")} | {"_startTime", "_endTime"}
    x = y = 0

    for title, desc, visual, text, w, h in QUERIES:
        qid = str(uuid.uuid5(NS, f"query:{PAGE}:{title}"))
        used = sorted(v for v in known if re.search(rf"(?<![\w]){re.escape(v)}(?![\w])", text))
        dash["queries"].append({
            "dataSource": {"kind": "inline", "dataSourceId": source_id},
            "text": text,
            "id": qid,
            "usedVariables": used,
        })
        if x + w > 24:
            x, y = 0, y + h
        dash["tiles"].append({
            "id": str(uuid.uuid5(NS, f"tile:{PAGE}:{title}")),
            "title": title,
            "description": desc,
            "visualType": visual,
            "pageId": page_id,
            "layout": {"x": x, "y": y, "width": w, "height": h},
            "visualOptions": VISUAL_OPTIONS[visual],
            "queryRef": {"kind": "query", "queryId": qid},
        })
        x += w
        if x >= 24:
            x, y = 0, y + h

    if x:
        y += h
    dash["tiles"].append({
        "id": str(uuid.uuid5(NS, f"tile:{PAGE}:explainer")),
        "title": "How to read this",
        "visualType": "markdownCard",
        "pageId": page_id,
        "layout": {"x": 0, "y": y, "width": 24, "height": 11},
        "markdownText": EXPLAINER,
        "visualOptions": {},
    })

    # No tile here reads _startTime/_endTime, so leaving the date picker visible
    # would offer a control that silently does nothing. Scope it to every page
    # except this one -- which is why this script has to run last.
    others = [p["id"] for p in dash["pages"] if p["id"] != page_id]
    for p in dash["parameters"]:
        if p.get("kind") == "duration":
            p["showOnPages"] = {"kind": "selection", "pageIds": others}

    # The one page that is worthless without it. Interval strings come from a
    # fixed enum; an arbitrary value like "45s" fails schema validation.
    dash["autoRefresh"] = {"enabled": True, "defaultInterval": "1m", "minInterval": "30s"}

    DASH.write_text(json.dumps(dash, indent=2) + "\n")
    tiles = sum(1 for t in dash["tiles"] if t["pageId"] == page_id)
    print(f"{PAGE}: {tiles} tiles, page id {page_id}")
    print(f"date picker hidden here, shown on {len(others)} other pages")


if __name__ == "__main__":
    main()
