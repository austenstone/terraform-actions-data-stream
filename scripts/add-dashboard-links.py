#!/usr/bin/env python3
"""Add GitHub deep links to dashboard tables with unambiguous targets.

Schema 77 keeps links separate from display values: KQL emits URL columns, then
table__renderLinks pairs each URL column with the readable column it wraps.
Run this after the page builders because they replace the queries they own.
"""

import json
import pathlib
import re


DASH = pathlib.Path(__file__).resolve().parent.parent / "modules/azure-kusto/dashboard/RealTimeDashboard.json"

LIVE_JOBS = r"""LiveJobs()
| lookup kind=leftouter (InFlightRuns() | project run_id, workflow_path) on run_id
| extend waiting = case(age_seconds < 90, strcat(tostring(age_seconds), "s"),
              age_seconds < 5400, strcat(tostring(round(age_seconds / 60.0, 1)), "m"),
              strcat(tostring(round(age_seconds / 3600.0, 1)), "h"))
// Not "queued vs running" -- the stream cannot distinguish those. The only
// honest split is whether anything with these labels has finished on a real
// runner in the last 24h.
| extend state = iff(label_served, "in flight", "STARVED: no runner serves these labels")
| extend repo_resolved = not(repo startswith "repo:"),
         workflow_file = extract(@"([^/]+)$", 1, workflow_path)
| extend repo_url = iff(repo_resolved,
                        strcat("https://github.com/", repo),
                        "")
| extend workflow_url = iff(repo_resolved and isnotempty(workflow_file),
                            strcat(repo_url, "/actions/workflows/", url_encode_component(workflow_file)), ""),
         branch_url = iff(repo_resolved and isnotempty(branch),
                          strcat(repo_url, "/tree/", url_encode_component(branch)), ""),
         run_url = iff(repo_resolved,
                       iff(attempt > 1,
                           strcat(repo_url, "/actions/runs/", run_id, "/attempts/", attempt),
                           strcat(repo_url, "/actions/runs/", run_id)), ""),
         job_url = iff(repo_resolved and isnotnull(check_run_id),
                       strcat(repo_url, "/actions/runs/", run_id, "/job/", check_run_id), "")
| project repo, workflow, job = job_name, labels, state, waiting, age_seconds,
          branch, trigger, run_id, repo_url, workflow_url, job_url, branch_url, run_url
| order by age_seconds desc"""

LIVE_RUNS = r"""LiveRuns()
| where not(phantom)
| lookup kind=leftouter (InFlightRuns() | project run_id, workflow_path) on run_id
| extend waiting = case(age_seconds < 90, strcat(tostring(age_seconds), "s"),
              age_seconds < 5400, strcat(tostring(round(age_seconds / 60.0, 1)), "m"),
              strcat(tostring(round(age_seconds / 3600.0, 1)), "h"))
| extend repo_resolved = not(repo startswith "repo:"),
         workflow_file = extract(@"([^/]+)$", 1, workflow_path)
| extend repo_url = iff(repo_resolved,
                        strcat("https://github.com/", repo),
                        "")
| extend workflow_url = iff(repo_resolved and isnotempty(workflow_file),
                            strcat(repo_url, "/actions/workflows/", url_encode_component(workflow_file)), ""),
         branch_url = iff(repo_resolved and isnotempty(branch),
                          strcat(repo_url, "/tree/", url_encode_component(branch)), ""),
         run_url = iff(repo_resolved,
                       iff(attempt > 1,
                           strcat(repo_url, "/actions/runs/", run_id, "/attempts/", attempt),
                           strcat(repo_url, "/actions/runs/", run_id)), "")
| project repo, workflow, branch, trigger, live_jobs, starved_jobs,
          waiting, age_seconds, started_at, run_id,
          repo_url, workflow_url, branch_url, run_url
| order by age_seconds desc"""

STREAM_STUCK_RUNS = r"""let _meta = Runs
| where isnull(completed_ev)
| extend d = todynamic(tostring(eventData))
| project run_id,
          repository_id = tolong(d.repository_id),
          attempt = toint(d.workflow_run_attempt),
          workflow = tostring(d.workflow.name),
          workflow_path = tostring(d.workflow_file_path),
          branch = tostring(d.workflow_run_head_branch);
StuckRuns()
| where created_at between (_startTime .. _endTime)
| where likely_dropped
| lookup kind=leftouter (_meta) on run_id
| lookup kind=leftouter (RepoNames() | project repository_id, repo) on repository_id
| extend repo = coalesce(repo, strcat("repo:", tostring(repository_id))),
         repo_resolved = isnotempty(repo) and not(repo startswith "repo:"),
         workflow_file = extract(@"([^/]+)$", 1, workflow_path)
| extend repo_url = iff(repo_resolved,
                        strcat("https://github.com/", repo),
                        "")
| extend workflow_url = iff(repo_resolved and isnotempty(workflow_file),
                            strcat(repo_url, "/actions/workflows/", url_encode_component(workflow_file)), ""),
         branch_url = iff(repo_resolved and isnotempty(branch),
                          strcat(repo_url, "/tree/", url_encode_component(branch)), ""),
         run_url = iff(repo_resolved,
                       iff(attempt > 1,
                           strcat(repo_url, "/actions/runs/", run_id, "/attempts/", attempt),
                           strcat(repo_url, "/actions/runs/", run_id)), "")
| project repo, workflow, branch, run_id, created_at,
          open_for = format_timespan(age, "d.hh:mm"), has_jobs,
          repo_url, workflow_url, branch_url, run_url
| order by created_at desc"""

EXPLORER_REPOS = r"""HierarchyJobs()
| where timestamp between (_startTime .. _endTime)
| where isempty(_org) or org == _org
| where isempty(_repo) or repo == _repo
| where isempty(_wf) or workflow == _wf
| summarize workflows = dcount(workflow, 4), jobs = count(),
            hours = round(sumif(exec_seconds, not(stranded)) / 3600.0, 2),
            failures = countif(conclusion == "failure"),
            ran = countif(ran)
  by org, repo, repository_id
| extend failure_pct = round(100.0 * failures / iff(ran == 0, 1, ran), 1),
         repo_resolved = not(repo startswith "repo/")
| extend repo_url = iff(repo_resolved,
                        strcat("https://github.com/", repo),
                        "")
| project org, repo, workflows, jobs, hours, failure_pct, repo_url
| order by hours desc
| take 200"""

EXPLORER_WORKFLOWS = r"""let _paths = HierarchyJobs()
| summarize repository_id = take_any(repository_id),
            workflow_path = take_any(workflow_path)
  by repo, workflow;
WorkflowSummary()
| where isempty(_org) or org == _org
| where isempty(_repo) or repo == _repo
| where isempty(_wf) or workflow == _wf
| lookup kind=leftouter (_paths) on repo, workflow
| extend repo_resolved = not(repo startswith "repo/"),
         workflow_file = extract(@"([^/]+)$", 1, workflow_path)
| extend repo_url = iff(repo_resolved,
                        strcat("https://github.com/", repo),
                        "")
| extend workflow_url = iff(repo_resolved and isnotempty(workflow_file),
                            strcat(repo_url, "/actions/workflows/", url_encode_component(workflow_file)), "")
| project org, repo, workflow, runs, jobs, p50, p95, queue_p95,
          failure_pct, exec_hours, exec_share_pct, repo_url, workflow_url
| order by exec_hours desc
| take 200"""

EXPLORER_JOBS = r"""let _paths = HierarchyJobs()
| summarize repository_id = take_any(repository_id),
            workflow_path = take_any(workflow_path)
  by repo, workflow;
JobSummary()
| where isempty(_org) or org == _org
| where isempty(_repo) or repo == _repo
| where isempty(_wf) or workflow == _wf
| where isempty(_job) or job_name == _job
| lookup kind=leftouter (_paths) on repo, workflow
| extend repo_resolved = not(repo startswith "repo/"),
         workflow_file = extract(@"([^/]+)$", 1, workflow_path)
| extend repo_url = iff(repo_resolved,
                        strcat("https://github.com/", repo),
                        "")
| extend workflow_url = iff(repo_resolved and isnotempty(workflow_file),
                            strcat(repo_url, "/actions/workflows/", url_encode_component(workflow_file)), "")
| project repo, workflow, job_name, runner, runs, p50, p95, max_seconds,
          queue_p95, failure_pct, exec_hours, repo_url, workflow_url
| order by exec_hours desc
| take 200"""

EXPLORER_RUNS = r"""HierarchyJobs()
| where timestamp between (_startTime .. _endTime)
| where isempty(_org) or org == _org
| where isempty(_repo) or repo == _repo
| where isempty(_wf) or workflow == _wf
| where isempty(_job) or job_name == _job
| summarize jobs = count(), started = min(started_at),
            wall_seconds = round(max(run_wall_seconds), 1),
            result = take_any(run_conclusion), workflow = take_any(workflow),
            repo = take_any(repo), repository_id = take_any(repository_id),
            workflow_path = take_any(workflow_path), attempt = take_any(attempt),
            failed_jobs = countif(conclusion == "failure")
  by run_id
| extend repo_resolved = not(repo startswith "repo/"),
         workflow_file = extract(@"([^/]+)$", 1, workflow_path)
| extend repo_url = iff(repo_resolved,
                        strcat("https://github.com/", repo),
                        "")
| extend workflow_url = iff(repo_resolved and isnotempty(workflow_file),
                            strcat(repo_url, "/actions/workflows/", url_encode_component(workflow_file)), ""),
         run_url = iff(repo_resolved,
                       iff(attempt > 1,
                           strcat(repo_url, "/actions/runs/", run_id, "/attempts/", attempt),
                           strcat(repo_url, "/actions/runs/", run_id)), "")
| project started, repo, workflow, run_id, jobs, failed_jobs, result, wall_seconds,
          repo_url, workflow_url, run_url
| order by started desc
| take 200"""

EXPLORER_TIMELINE = r"""// Kusto dashboards have no gantt or flame visual, so the span bars are drawn
// with characters and rendered in a table. Falls back to a run in the current
// selection so the tile is never blank on first load.
let _scoped = HierarchyJobs()
| where timestamp between (_startTime .. _endTime)
| where isempty(_org) or org == _org
| where isempty(_repo) or repo == _repo
| where isempty(_wf) or workflow == _wf
| where isempty(_job) or job_name == _job
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
let _ctx = _scoped
| where run_id == _target
| summarize repo = take_any(repo), attempt = take_any(attempt);
let _target_repo = toscalar(_ctx | project repo);
let _target_attempt = toscalar(_ctx | project attempt);
let _run_url = iff(isnotempty(_target_repo) and not(_target_repo startswith "repo/"),
                   iff(_target_attempt > 1,
                       strcat("https://github.com/", _target_repo, "/actions/runs/", _target,
                              "/attempts/", _target_attempt),
                       strcat("https://github.com/", _target_repo, "/actions/runs/", _target)), "");
RunTimeline(_target)
| extend run_id = _target, run_url = _run_url
| project label, seconds, conclusion, gantt, started_at, run_id, run_url"""

EXPLORER_STRANDED = r"""// Jobs that asked for a label nothing serves. They never got a runner, so the
// stream reports their queue wait as execution time and they sit until the 24h
// timeout cancels them. Excluded from the statistics above, listed here because
// each one is a workflow still pinning a dead runner label.
HierarchyJobs()
| where stranded
| where timestamp between (_startTime .. _endTime)
| where isempty(_org) or org == _org
| where isempty(_repo) or repo == _repo
| where isempty(_wf) or workflow == _wf
| extend label = tostring(todynamic(labels)[0]),
         waited_hours = round(exec_seconds / 3600.0, 2),
         repo_resolved = not(repo startswith "repo/"),
         workflow_file = extract(@"([^/]+)$", 1, workflow_path)
| extend repo_url = iff(repo_resolved,
                        strcat("https://github.com/", repo),
                        "")
| extend workflow_url = iff(repo_resolved and isnotempty(workflow_file),
                            strcat(repo_url, "/actions/workflows/", url_encode_component(workflow_file)), ""),
         run_url = iff(repo_resolved,
                       iff(attempt > 1,
                           strcat(repo_url, "/actions/runs/", run_id, "/attempts/", attempt),
                           strcat(repo_url, "/actions/runs/", run_id)), ""),
         job_url = iff(repo_resolved and isnotnull(check_run_id),
                       strcat(repo_url, "/actions/runs/", run_id, "/job/", check_run_id), "")
| project timestamp, repo, workflow, job_name, label, conclusion, waited_hours, run_id,
          repo_url, workflow_url, job_url, run_url
| order by waited_hours desc
| take 100"""

SLOWEST_JOBS = r"""HierarchyJobs()
| where queued_at between (_startTime .. _endTime)
| where isnull(_workflow) or array_length(_workflow) == 0 or workflow in (_workflow)
| where runner_kind != "unknown"
| where queue_seconds >= 0
| extend Label = tostring(parse_json(labels)[0]),
         repo_resolved = not(repo startswith "repo/"),
         workflow_file = extract(@"([^/]+)$", 1, workflow_path)
| extend repo_url = iff(repo_resolved,
                        strcat("https://github.com/", repo),
                        "")
| extend workflow_url = iff(repo_resolved and isnotempty(workflow_file),
                            strcat(repo_url, "/actions/workflows/", url_encode_component(workflow_file)), ""),
         run_url = iff(repo_resolved,
                       iff(attempt > 1,
                           strcat(repo_url, "/actions/runs/", run_id, "/attempts/", attempt),
                           strcat(repo_url, "/actions/runs/", run_id)), ""),
         job_url = iff(repo_resolved and isnotnull(check_run_id),
                       strcat(repo_url, "/actions/runs/", run_id, "/job/", check_run_id), "")
| project Job = job_name, Workflow = workflow, Repo = repo, Label,
          ["Queued s"] = queue_seconds, ["Ran s"] = exec_seconds,
          Conclusion = conclusion, When = queued_at, Run = run_id,
          repo_url, workflow_url, job_url, run_url
| top 20 by ["Queued s"] desc"""


SPECS = {
    ("Live", "Jobs in flight"): (
        LIVE_JOBS,
        [("repo_url", "repo"), ("workflow_url", "workflow"), ("job_url", "job"),
         ("branch_url", "branch"), ("run_url", "run_id")],
    ),
    ("Live", "Runs in flight"): (
        LIVE_RUNS,
        [("repo_url", "repo"), ("workflow_url", "workflow"),
         ("branch_url", "branch"), ("run_url", "run_id")],
    ),
    ("Stream health", "Runs that never completed"): (
        STREAM_STUCK_RUNS,
        [("repo_url", "repo"), ("workflow_url", "workflow"),
         ("branch_url", "branch"), ("run_url", "run_id")],
    ),
    ("Explorer", "Repositories"): (
        EXPLORER_REPOS,
        [("repo_url", "repo")],
    ),
    ("Explorer", "Workflows"): (
        EXPLORER_WORKFLOWS,
        [("repo_url", "repo"), ("workflow_url", "workflow")],
    ),
    ("Explorer", "Jobs"): (
        EXPLORER_JOBS,
        [("repo_url", "repo"), ("workflow_url", "workflow")],
    ),
    ("Explorer", "Runs in scope"): (
        EXPLORER_RUNS,
        [("repo_url", "repo"), ("workflow_url", "workflow"), ("run_url", "run_id")],
    ),
    ("Explorer", "Run trace"): (
        EXPLORER_TIMELINE,
        [("run_url", "label")],
    ),
    ("Explorer", "Stranded jobs"): (
        EXPLORER_STRANDED,
        [("repo_url", "repo"), ("workflow_url", "workflow"),
         ("job_url", "job_name"), ("run_url", "run_id")],
    ),
    ("Queue & runners", "Slowest jobs to get a runner"): (
        SLOWEST_JOBS,
        [("repo_url", "Repo"), ("workflow_url", "Workflow"),
         ("job_url", "Job"), ("run_url", "Run")],
    ),
}


def declared_variables(doc):
    variables = set()
    for parameter in doc["parameters"]:
        if parameter.get("kind") == "duration":
            variables.update(
                parameter.get(key)
                for key in ("beginVariableName", "startVariableName", "endVariableName")
                if parameter.get(key)
            )
        elif parameter.get("variableName"):
            variables.add(parameter["variableName"])
    return variables


def main():
    doc = json.loads(DASH.read_text())
    pages = {page["id"]: page["name"] for page in doc["pages"]}
    queries = {query["id"]: query for query in doc["queries"]}
    variables = declared_variables(doc)
    found = set()

    for tile in doc["tiles"]:
        key = (pages[tile["pageId"]], tile.get("title"))
        if key not in SPECS:
            continue
        if tile.get("visualType") != "table":
            raise SystemExit(f"{key} is no longer a table")

        text, links = SPECS[key]
        query = queries[tile["queryRef"]["queryId"]]
        query["text"] = text
        query["usedVariables"] = sorted(
            variable
            for variable in variables
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(variable)}\b", text)
        )

        options = tile.setdefault("visualOptions", {})
        options["table__enableRenderLinks"] = True
        options["table__renderLinks"] = [
            {"urlColumn": url, "displayColumn": display, "disabled": False}
            for url, display in links
        ]
        found.add(key)

    missing = set(SPECS) - found
    if missing:
        raise SystemExit(f"missing expected table tiles: {sorted(missing)}")

    DASH.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"Added {sum(len(links) for _, links in SPECS.values())} links across {len(SPECS)} tables")


if __name__ == "__main__":
    main()
