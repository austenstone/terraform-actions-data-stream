#!/usr/bin/env python3
"""Add (or replace) the "Stream health" page in the Kusto dashboard.

The dashboard JSON is hand-maintained, but this page is generated because its
queries are the ones most likely to change as preview bugs get fixed. Re-run it
after editing QUERIES below, then validate before importing.
"""

import json
import pathlib
import re
import uuid

DASH = pathlib.Path(__file__).resolve().parent.parent / "modules/azure-kusto/dashboard/RealTimeDashboard.json"
NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
PAGE = "Stream health"

# Test connection writes a real row into the destination as an undocumented
# sixth event type. Filtered here so it can't inflate throughput. See issue #15.
WINDOW = """let _ev = materialize(ActionsEvents
| where eventTimestamp between (_startTime .. _endTime)
// "Test connection" writes a real row here as an undocumented `test_connection`
// event whose eventData is just {"is_new_sink": true}. Excluded so debugging a
// sink doesn't show up as traffic. https://github.com/austenstone/terraform-actions-data-stream/issues/15
| where eventType != "test_connection");"""

SCORECARD = (
    WINDOW
    + """
// count()-minus-dcount() is NOT a duplicate detector: dcount is HyperLogLog and
// was off by 51 on 21,915 rows even at accuracy 4. Count the collisions instead.
let _dupes = toscalar(_ev | summarize c = count() by eventUuid | summarize countif(c > 1));
let _dropped = toscalar(StuckRuns()
    | where created_at between (_startTime .. _endTime)
    | summarize countif(likely_dropped));
let _skipped = toscalar(JobFacts()
    | where queued_at between (_startTime .. _endTime)
    | summarize round(100.0 * countif(not(ran)) / count(), 1));
_ev
| extend lag_s = todouble(datetime_diff("millisecond", ingestion_time(), eventTimestamp)) / 1000.0
| summarize events = count(),
            p95 = round(percentile(iff(lag_s >= 0, lag_s, real(null)), 95), 1)
| extend Rows = pack_array(
    pack_array("Events ingested", tostring(events)),
    pack_array("Ingest lag p95", strcat(tostring(p95), " s")),
    pack_array("Duplicate events", tostring(_dupes)),
    pack_array("Dropped completions", tostring(_dropped)),
    pack_array("Jobs skipped", strcat(tostring(_skipped), " %")))
| mv-expand Rows
| project title = tostring(Rows[0]), value = tostring(Rows[1])"""
)

LAG_TREND = (
    WINDOW
    + """
_ev
| extend lag_s = todouble(datetime_diff("millisecond", ingestion_time(), eventTimestamp)) / 1000.0
| where lag_s >= 0
| summarize p50 = round(percentile(lag_s, 50), 2),
            p95 = round(percentile(lag_s, 95), 2),
            p99 = round(percentile(lag_s, 99), 2)
    by timestamp = bin(eventTimestamp, 10m)
| order by timestamp asc"""
)

PAIRING = (
    WINDOW
    + """
// Every created event should eventually be matched by a completed one. Runs that
// dispatch no job never emit a completion at all -- see issue #8. A small negative
// on Jobs is a window-boundary artifact, not a leak.
_ev
| summarize c = count() by eventType
| extend entity = iff(eventType startswith "workflow_run", "Runs", "Jobs"),
         phase = extract(@"_(created|completed)$", 1, eventType)
| summarize opened = sumif(c, phase == "created"), closed = sumif(c, phase == "completed") by entity
| extend never_closed = opened - closed,
         leak_pct = round(100.0 * (opened - closed) / opened, 2)
| order by entity asc"""
)

STUCK = """StuckRuns()
| where created_at between (_startTime .. _endTime)
| where likely_dropped
| project run_id, created_at, open_for = format_timespan(age, "d.hh:mm"), has_jobs
| order by created_at desc"""

EXPLAINER = """### Why this page exists

`sink_health` only advances on connection tests, so a sink that has delivered nothing
for an hour still reports `healthy`. **Health is failure-fast but success-blind** --
this page is the success signal.

- **Ingest lag** is `ingestion_time() - eventTimestamp`. Measured p50 ~5s, p95 ~7s.
  A sustained climb means the destination is backing up, not that GitHub is slow.
- **Duplicate events** should be 0. Delivery is at-least-once by contract, so a
  non-zero value here is expected behaviour, not an incident -- dedupe on `eventUuid`.
- **Dropped completions** are runs that emitted `created` and never `completed`
  because they dispatched no job. ~1.2% of runs. They are not stuck, they are gone.
- **Jobs skipped** is ~40% on a busy org. Skipped jobs carry all-zero timings and
  must be filtered before any duration maths -- `JobFacts()` exposes `ran` for this.

Zero events with a `healthy` sink is the alert worth wiring: `ActionsEvents | where
eventTimestamp > ago(30m) | count`."""

QUERIES = [
    ("Stream health", "Delivery and data-quality signal for the selected window", "multistat", SCORECARD, 24, 6),
    ("Ingestion lag", "Time from event to queryable, in seconds", "line", LAG_TREND, 12, 7),
    ("Completeness", "Created events that never got a completion", "table", PAIRING, 12, 7),
    ("Runs that never completed", "Dispatched no job, so no completion is coming", "table", STUCK, 24, 8),
]

VISUAL_OPTIONS = {
    "multistat": {
        "multiStat__valueColumn": "value",
        "multiStat__labelColumn": "title",
        "multiStat__displayOrientation": "horizontal",
        "multiStat__slot": {"width": 5, "height": 1},
        "multiStat__textSize": "auto",
        "colorRules": [],
        "colorRulesDisabled": True,
        "colorStyle": "light",
    },
    "line": {
        "xColumn": "timestamp",
        "yColumns": ["p50", "p95", "p99"],
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
    keep = {t["queryRef"]["queryId"] for t in dash["tiles"] if t["pageId"] != page_id and "queryRef" in t}
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
        "layout": {"x": 0, "y": y, "width": 24, "height": 9},
        "markdownText": EXPLAINER,
        "visualOptions": {},
    })

    DASH.write_text(json.dumps(dash, indent=2) + "\n")
    print(f"{PAGE}: {len(QUERIES) + 1} tiles on page {page_id}")


if __name__ == "__main__":
    main()
