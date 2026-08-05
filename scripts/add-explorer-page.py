#!/usr/bin/env python3
"""Add (or replace) the "Explorer" page in the Kusto dashboard.

One page that walks org > repo > workflow > job > step. Every table cross-filters
into the next level's parameter, so clicking a repo narrows the workflow table,
clicking a workflow narrows the jobs table, and so on down to the per-run flame
graph. The same parameters are also dropdowns, so the page is navigable without
knowing where to click first.

The stream itself carries no enterprise column -- one sink is scoped to one
enterprise or one org, so the stream *is* that scope. Org is the honest top of
the tree; see the note above HierarchyJobs() in kql/enrichment.kql.

Re-run after editing, then validate before importing:
    python3 scripts/add-explorer-page.py
    python3 <notes>/.github/skills/fabric-dashboards/scripts/validate_dashboard.py \
        modules/azure-kusto/dashboard/RealTimeDashboard.json
"""

import json
import pathlib
import re
import uuid

DASH = pathlib.Path(__file__).resolve().parent.parent / "modules/azure-kusto/dashboard/RealTimeDashboard.json"
NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
PAGE = "Explorer"


def uid(name):
    """Stable ids so re-running edits the same page instead of duplicating it."""
    return str(uuid.uuid5(NS, f"explorer:{name}"))


# Parameters. Scalar ones get a cascading dropdown; run id gets a free-text box
# because a list of several thousand ids is not something anyone browses.
P_ORG, P_REPO, P_WF, P_JOB, P_RUN = (uid(f"param:{n}") for n in
                                     ("org", "repo", "wf", "job", "run"))

# Applied identically everywhere so the tiles cannot disagree about what is
# selected. An unset scalar parameter arrives as null, which isempty() covers.
FILTER = """| where isempty(_org) or org == _org
| where isempty(_repo) or repo == _repo
| where isempty(_wf) or workflow == _wf"""

JOB_FILTER = FILTER + """
| where isempty(_job) or job_name == _job"""

QUERIES = {
    # ---- dropdown sources. Kept cheap: they run on every page load, and each
    # one is already narrowed by the level above it.
    "opt_org": """Repos
| where resolved
| summarize by owner
| where isnotempty(owner)
| project value = owner, label = owner
| order by label asc""",
    "opt_repo": """Repos
| where resolved
| where isempty(_org) or owner == _org
| summarize by full_name
| project value = full_name, label = full_name
| order by label asc
| take 2000""",
    "opt_wf": """HierarchyJobs()
| where timestamp between (_startTime .. _endTime)
| where isempty(_org) or org == _org
| where isempty(_repo) or repo == _repo
| summarize by workflow
| project value = workflow, label = workflow
| order by label asc
| take 2000""",
    "opt_job": """HierarchyJobs()
| where timestamp between (_startTime .. _endTime)
"""
    + FILTER
    + """
| summarize by job_name
| project value = job_name, label = job_name
| order by label asc
| take 2000""",

    # ---- breadcrumb. Tile titles are static strings with no interpolation, so
    # the current selection has to be rendered as data.
    "crumb": """let _sel = HierarchyJobs()
| where timestamp between (_startTime .. _endTime)
"""
    + JOB_FILTER
    + """;
let _n = toscalar(_sel | summarize count());
let _hrs = toscalar(_sel | where not(stranded) | summarize round(sum(exec_seconds) / 3600.0, 1));
print n = _n, hrs = _hrs
| extend Rows = pack_array(
    pack_array("Organization", iff(isempty(_org), "all", _org)),
    pack_array("Repository", iff(isempty(_repo), "all", _repo)),
    pack_array("Workflow", iff(isempty(_wf), "all", _wf)),
    pack_array("Job", iff(isempty(_job), "all", _job)),
    pack_array("Jobs in scope", tostring(n)),
    pack_array("Compute hours", tostring(hrs)))
| mv-expand Rows
| project title = tostring(Rows[0]), value = tostring(Rows[1])""",

    # ---- how much tree is under the current selection
    "shape": """let _h = HierarchyJobs()
| where timestamp between (_startTime .. _endTime)
"""
    + JOB_FILTER
    + """;
let _s = HierarchySteps()
| where timestamp between (_startTime .. _endTime)
"""
    + JOB_FILTER
    + """;
union
  (_h | summarize level = "organizations", depth = 1, count_ = dcountif(org, org != "unresolved", 4)),
  (_h | summarize level = "repositories", depth = 2, count_ = dcount(repo, 4)),
  (_h | summarize level = "workflows", depth = 3, count_ = dcount(strcat(repo, "/", workflow), 4)),
  (_h | summarize level = "jobs", depth = 4, count_ = dcount(strcat(repo, "/", workflow, "/", job_name), 4)),
  (_s | summarize level = "steps", depth = 5, count_ = dcount(strcat(job_name, "/", step_name), 4))
| order by depth asc
| project level, distinct_names = count_""",

    # ---- level 2
    "repos": """HierarchyJobs()
| where timestamp between (_startTime .. _endTime)
"""
    + FILTER
    + """
| summarize workflows = dcount(workflow, 4), jobs = count(),
            hours = round(sumif(exec_seconds, not(stranded)) / 3600.0, 2),
            failures = countif(conclusion == "failure"),
            ran = countif(ran)
  by org, repo
| extend failure_pct = round(100.0 * failures / iff(ran == 0, 1, ran), 1)
| project org, repo, workflows, jobs, hours, failure_pct
| order by hours desc
| take 200""",

    # ---- level 3
    "workflows": """WorkflowSummary()
"""
    + FILTER
    + """
| project org, repo, workflow, runs, jobs, p50, p95, queue_p95,
          failure_pct, exec_hours, exec_share_pct
| order by exec_hours desc
| take 200""",

    # ---- level 4
    "jobs": """JobSummary()
"""
    + JOB_FILTER
    + """
| project repo, workflow, job_name, runner, runs, p50, p95, max_seconds,
          queue_p95, failure_pct, exec_hours
| order by exec_hours desc
| take 200""",

    # ---- level 5. Only covers runs whose steps have been ingested from the job
    # API, so the counts here are a sample of the levels above, not a superset.
    "steps": """HierarchySteps()
| where timestamp between (_startTime .. _endTime)
"""
    + JOB_FILTER
    + """
| where ran
| summarize occurrences = count(),
            p50 = round(percentile(duration_seconds, 50), 1),
            p95 = round(percentile(duration_seconds, 95), 1),
            minutes = round(sum(duration_seconds) / 60.0, 1),
            failures = countif(step_conclusion == "failure")
  by workflow, job_name, step_name
| extend failure_pct = round(100.0 * failures / occurrences, 1)
| project workflow, job_name, step_name, occurrences, p50, p95, minutes, failure_pct
| order by minutes desc
| take 200""",

    # ---- pick a run to trace
    "runs": """HierarchyJobs()
| where timestamp between (_startTime .. _endTime)
"""
    + JOB_FILTER
    + """
| summarize jobs = count(), started = min(started_at),
            wall_seconds = round(max(run_wall_seconds), 1),
            result = any(run_conclusion), workflow = any(workflow),
            repo = any(repo), failed_jobs = countif(conclusion == "failure")
  by run_id
| project started, repo, workflow, run_id, jobs, failed_jobs, result, wall_seconds
| order by started desc
| take 200""",

    # ---- the flame graph, as text
    "timeline": """// Kusto dashboards have no gantt or flame visual, so the span bars are drawn
// with characters and rendered in a table. Falls back to a run in the current
// selection so the tile is never blank on first load.
let _scoped = HierarchyJobs()
| where timestamp between (_startTime .. _endTime)
"""
    + JOB_FILTER
    + """
// Stranded jobs are excluded or the fallback always lands on a 24h timeout,
// whose trace is one flat bar and tells you nothing.
| where not(stranded) and isnotnull(run_wall_seconds);
// Prefer a run whose steps were ingested, so the default trace shows all three
// levels rather than a bare run > job pair.
let _rich = toscalar(_scoped
    | join kind=leftsemi (HierarchySteps() | distinct run_id) on run_id
    | summarize arg_max(run_wall_seconds, run_id)
    | project run_id);
let _any = toscalar(_scoped | summarize arg_max(run_wall_seconds, run_id) | project run_id);
let _target = case(isnotempty(_run), tolong(_run), isnotnull(_rich), _rich, _any);
RunTimeline(_target)""",

    # ---- the jobs deliberately excluded from every number above
    "stranded": """// Jobs that asked for a label nothing serves. They never got a runner, so the
// stream reports their queue wait as execution time and they sit until the 24h
// timeout cancels them. Excluded from the statistics above, listed here because
// each one is a workflow still pinning a dead runner label.
StrandedJobs()
| where timestamp between (_startTime .. _endTime)
"""
    + FILTER
    + """
| project timestamp, repo, workflow, job_name, label, conclusion, waited_hours, run_id
| order by waited_hours desc
| take 100""",
}

TABLE_OPTS = {
    "colorRulesDisabled": True,
    "colorRules": [],
    "table__enableRenderLinks": True,
    "table__renderLinks": [],
    "crossFilterDisabled": False,
    "drillthroughDisabled": False,
    "crossFilter": [],
    "drillthrough": [],
}


def table(name, title, desc, x, y, w, h, cross=()):
    opts = json.loads(json.dumps(TABLE_OPTS))
    opts["crossFilter"] = [
        {"interaction": "column", "property": col, "parameterId": pid, "disabled": False}
        for col, pid in cross
    ]
    return {
        "id": uid(f"tile:{name}"),
        "title": title,
        "description": desc,
        "visualType": "table",
        "pageId": uid("page"),
        "layout": {"x": x, "y": y, "width": w, "height": h},
        "visualOptions": opts,
        "queryRef": {"kind": "query", "queryId": uid(f"query:{name}")},
    }


TILES = [
    {
        "id": uid("tile:crumb"),
        "title": "Current selection",
        "description": "Click any row below to drill in; use the dropdowns to jump",
        "visualType": "multistat",
        "pageId": uid("page"),
        "layout": {"x": 0, "y": 0, "width": 12, "height": 9},
        "visualOptions": {
            "multiStat__valueColumn": "value",
            "multiStat__labelColumn": "title",
            "multiStat__displayOrientation": "horizontal",
            "multiStat__slot": {"width": 2, "height": 3},
            "multiStat__textSize": "auto",
            "colorRules": [],
            "colorRulesDisabled": False,
            "colorStyle": "light",
        },
        "queryRef": {"kind": "query", "queryId": uid("query:crumb")},
    },
    table("shape", "Tree below this selection", "Distinct names at each level",
          12, 0, 12, 9),
    table("repos", "Repositories", "Click a row to filter everything below",
          0, 9, 12, 8, cross=[("org", P_ORG), ("repo", P_REPO)]),
    table("workflows", "Workflows", "Duration percentiles and share of compute",
          12, 9, 12, 8, cross=[("repo", P_REPO), ("workflow", P_WF)]),
    table("jobs", "Jobs", "The level the stream actually emits",
          0, 17, 12, 8, cross=[("workflow", P_WF), ("job_name", P_JOB)]),
    table("steps", "Steps", "From the job API; covers a subset of runs",
          12, 17, 12, 8, cross=[("job_name", P_JOB)]),
    table("runs", "Runs in scope", "Click a run to trace it",
          0, 25, 12, 8, cross=[("run_id", P_RUN)]),
    table("timeline", "Run trace", "run > job > step, longest run if none picked",
          12, 25, 12, 8),
    table("stranded", "Stranded jobs", "Asked for a label nothing serves",
          0, 33, 24, 6),
]

PARAMS = [
    {
        "id": P_ORG, "displayName": "Organization", "description": "",
        "kind": "string", "variableName": "_org", "selectionType": "scalar",
        "includeAllOption": True, "defaultValue": {"kind": "all"},
        "dataSource": {
            "kind": "query",
            "queryRef": {"kind": "query", "queryId": uid("query:opt_org")},
            "columns": {"value": "value", "label": "label"},
        },
        "showOnPages": {"kind": "selection", "pageIds": [uid("page")]},
    },
    {
        "id": P_REPO, "displayName": "Repository", "description": "",
        "kind": "string", "variableName": "_repo", "selectionType": "scalar",
        "includeAllOption": True, "defaultValue": {"kind": "all"},
        "dataSource": {
            "kind": "query",
            "queryRef": {"kind": "query", "queryId": uid("query:opt_repo")},
            "columns": {"value": "value", "label": "label"},
        },
        "showOnPages": {"kind": "selection", "pageIds": [uid("page")]},
    },
    {
        "id": P_WF, "displayName": "Workflow name", "description": "",
        "kind": "string", "variableName": "_wf", "selectionType": "scalar",
        "includeAllOption": True, "defaultValue": {"kind": "all"},
        "dataSource": {
            "kind": "query",
            "queryRef": {"kind": "query", "queryId": uid("query:opt_wf")},
            "columns": {"value": "value", "label": "label"},
        },
        "showOnPages": {"kind": "selection", "pageIds": [uid("page")]},
    },
    {
        "id": P_JOB, "displayName": "Job", "description": "",
        "kind": "string", "variableName": "_job", "selectionType": "scalar",
        "includeAllOption": True, "defaultValue": {"kind": "all"},
        "dataSource": {
            "kind": "query",
            "queryRef": {"kind": "query", "queryId": uid("query:opt_job")},
            "columns": {"value": "value", "label": "label"},
        },
        "showOnPages": {"kind": "selection", "pageIds": [uid("page")]},
    },
    {
        # Free text: run ids are not something anyone picks off a list, and a
        # dropdown of every id in the window would be several thousand rows.
        "id": P_RUN, "displayName": "Run id", "description": "",
        "kind": "string", "variableName": "_run", "selectionType": "freetext",
        "defaultValue": {"kind": "value", "value": ""},
        "showOnPages": {"kind": "selection", "pageIds": [uid("page")]},
    },
]

VARS = ["_startTime", "_endTime", "_org", "_repo", "_wf", "_job", "_run"]


def main():
    d = json.loads(DASH.read_text())
    ds = d["dataSources"][0]["id"]
    page_id = uid("page")

    # Wipe any previous build of this page so re-running is idempotent even if
    # tiles were renamed or dropped between runs.
    ours = {uid(f"query:{n}") for n in QUERIES}
    d["tiles"] = [t for t in d["tiles"] if t.get("pageId") != page_id]
    d["queries"] = [q for q in d["queries"] if q["id"] not in ours]
    d["parameters"] = [p for p in d["parameters"] if p["id"] not in
                       {P_ORG, P_REPO, P_WF, P_JOB, P_RUN}]
    d["pages"] = [p for p in d["pages"] if p["id"] != page_id]

    for name, text in QUERIES.items():
        d["queries"].append({
            "id": uid(f"query:{name}"),
            "dataSource": {"kind": "inline", "dataSourceId": ds},
            "text": text,
            # A referenced variable that is missing here fails at render time
            # with "Failed to resolve scalar expression", so derive it instead
            # of hand-maintaining the list.
            # The lookbehind stops the tail of a longer identifier or a string
            # literal (say "workflow_run") from reading as the variable _run.
            "usedVariables": [v for v in VARS
                              if re.search(rf"(?<![A-Za-z0-9_]){v}\b", text)],
        })

    d["pages"].append({"name": PAGE, "id": page_id})
    d["parameters"].extend(PARAMS)
    d["tiles"].extend(TILES)

    DASH.write_text(json.dumps(d, indent=2) + "\n")
    print(f"{PAGE}: {len(TILES)} tiles, {len(QUERIES)} queries, {len(PARAMS)} parameters")
    print(f"pages now: {[p['name'] for p in d['pages']]}")


if __name__ == "__main__":
    main()
