#!/usr/bin/env python3
"""Archive GitHub Actions workflow logs into Kusto, triggered by the Actions Data Stream.

Data Stream = trigger (tells you a run finished), REST API = payload (the log bytes).
Uses the undocumented ID-based logs route so no owner/repo lookup is needed:
    GET /repositories/{repository_id}/actions/runs/{run_id}/attempts/{n}/logs
"""
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor

CLUSTER = os.environ["KUSTO_CLUSTER"].rstrip("/")
DB = os.environ.get("KUSTO_DATABASE", "ActionsDataStream")
TOKEN = os.environ.get("KUSTO_TOKEN") or subprocess.run(
    ["az", "account", "get-access-token", "--resource", CLUSTER,
     "--query", "accessToken", "-o", "tsv"],
    capture_output=True, text=True, check=True).stdout.strip()

LINE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z) (.*)$")
MARK = re.compile(r"^##\[(\w+)\](.*)$")
JOBFILE = re.compile(r"^(\d+)_(.+)\.txt$")


def kusto(path, body, tries=6):
    req = urllib.request.Request(
        CLUSTER + path, data=body.encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    for attempt in range(tries):
        try:
            return urllib.request.urlopen(req).read().decode()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < tries - 1:
                time.sleep(2 ** attempt)
                continue
            raise


def query(csl):
    r = json.loads(kusto("/v2/rest/query", json.dumps({"db": DB, "csl": csl})))
    frame = next(f for f in r if f.get("TableKind") == "PrimaryResult")
    cols = [c["ColumnName"] for c in frame["Columns"]]
    return [dict(zip(cols, row)) for row in frame["Rows"]]


def ingest(table, mapping, rows, chunk=5000):
    """Inline ingest via the engine. POC-scale; production queues from blob instead."""
    for i in range(0, len(rows), chunk):
        nd = "\n".join(json.dumps(r) for r in rows[i:i + chunk])
        cmd = (f".ingest inline into table {table} with "
               f"(format='json', ingestionMappingReference='{mapping}') <|\n{nd}")
        kusto("/v1/rest/mgmt", json.dumps({"db": DB, "csl": cmd}))
    return len(rows)


def parse_job(name, text, repo_id, run_id, attempt):
    """One row per log line.

    ##[group]/##[endgroup] rows are RETAINED (severity 'group'/'endgroup') so step
    spans get exact boundaries rather than min/max of whatever happened to be logged.
    """
    m = JOBFILE.match(name)
    idx, job = (int(m.group(1)), m.group(2)) if m else (-1, name.rsplit("/", 1)[0])
    step, out, n = "", [], 0
    for raw in text.splitlines():
        lm = LINE.match(raw.lstrip("\ufeff"))
        if not lm:
            continue
        ts, msg = lm.group(1), lm.group(2)
        n += 1
        sev = "info"
        mk = MARK.match(msg)
        if mk:
            kind, rest = mk.group(1), mk.group(2)
            if kind == "group":
                step, sev, msg = rest, "group", rest
            elif kind == "endgroup":
                sev, msg = "endgroup", step
            elif kind in ("error", "warning", "notice"):
                sev, msg = kind, rest
            else:
                msg = rest
        out.append({"timestamp": ts, "repo": str(repo_id), "run_id": run_id,
                    "attempt": attempt, "job_name": job, "job_index": idx, "step": step,
                    "severity": sev, "message": msg[:32000], "line_no": n})
        if mk and mk.group(1) == "endgroup":
            step = ""
    return out


def fetch(repo_id, run_id, attempt):
    url = f"/repositories/{repo_id}/actions/runs/{run_id}/attempts/{attempt}/logs"
    env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}
    p = subprocess.run(["gh", "api", url], capture_output=True, env=env)
    return p.stdout if p.returncode == 0 else None


def process(job):
    """Fetch + parse one run. Pure, thread-safe, no Kusto calls."""
    rid, run, att = job
    blob = fetch(rid, run, att)
    if not blob:
        return None
    try:
        z = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        return None
    names = [n for n in z.namelist() if n.endswith(".txt") and "/" not in n]
    rows, raw = [], 0
    for n in names:
        data = z.read(n).decode("utf-8", "replace")
        raw += len(data)
        rows += parse_job(n, data, rid, run, att)
    return {"run": run, "att": att, "rid": rid, "rows": rows,
            "jobs": len(names), "zip": len(blob), "raw": raw}


def main(limit=25, workers=6, flush=20000):
    todo = query(f"""
    let done = LogArchive | project run_id, attempt;
    ActionsEvents
    | where eventType == 'workflow_run_completed' and eventTimestamp > ago(7d)
    // skipped/cancelled runs return 200 with a 22-byte empty zip -- every one is a
    // wasted call plus a junk zero-line LogArchive row. Was 30/30 of our empties.
    | where tostring(eventData.workflow_run_conclusion) !in ('skipped', 'cancelled')
    | project run_id = tolong(eventData.workflow_run_id),
              attempt = toint(eventData.workflow_run_attempt),
              repo_id = tolong(eventData.repository_id)
    | distinct run_id, attempt, repo_id
    | join kind=leftanti done on run_id, attempt
    | take {limit}""")
    print(f"{len(todo)} runs to archive")
    jobs = [(int(r["repo_id"]), int(r["run_id"]), int(r["attempt"])) for r in todo]

    buf_logs, buf_arch = [], []
    tl = tb = ok = skip = 0
    t0 = time.time()
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    with ThreadPoolExecutor(workers) as pool:
        for i, res in enumerate(pool.map(process, jobs), 1):
            if res is None:
                skip += 1
                continue
            ok += 1
            buf_logs += res["rows"]
            buf_arch.append({"run_id": res["run"], "attempt": res["att"],
                             "repo": str(res["rid"]), "archived_at": now,
                             "job_count": res["jobs"], "line_count": len(res["rows"]),
                             "bytes_zip": res["zip"], "bytes_raw": res["raw"]})
            tl += len(res["rows"]); tb += res["zip"]
            if len(buf_logs) >= flush:
                ingest("WorkflowLogs", "WorkflowLogsMapping", buf_logs)
                ingest("LogArchive", "LogArchiveMapping", buf_arch)
                print(f"  flushed {len(buf_logs)} lines ({i}/{len(jobs)} runs)")
                buf_logs, buf_arch = [], []

    if buf_logs:
        ingest("WorkflowLogs", "WorkflowLogsMapping", buf_logs)
        ingest("LogArchive", "LogArchiveMapping", buf_arch)
        print(f"  flushed {len(buf_logs)} lines (final)")

    el = time.time() - t0
    print(f"\n{ok} archived, {skip} unavailable | {tl} lines, {tb/1024/1024:.1f}MB zip "
          f"| {el:.0f}s ({tl/el:.0f} lines/s)")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 25)
