#!/usr/bin/env python3
"""Ingest real step-level facts into Kusto.

Data Stream gives check_run_id (the public REST job id -- NOT job_id, which is
internal). That key alone resolves the job, its display name, and every step
with exact timings. No log parsing, no filename matching.
"""
import json, os, subprocess, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

CLUSTER = os.environ["KUSTO_CLUSTER"].rstrip("/")
DB, CHUNK = os.environ.get("KUSTO_DATABASE", "ActionsDataStream"), 5000
# Must match var.table_name if you changed it from the module default.
RAW = os.environ.get("KUSTO_TABLE", "ActionsEvents")
TOKEN = os.environ.get("KUSTO_TOKEN") or subprocess.run(
    ["az", "account", "get-access-token", "--resource", CLUSTER,
     "--query", "accessToken", "-o", "tsv"],
    capture_output=True, text=True, check=True).stdout.strip()
ENV = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}
MISS = []


def kusto(path, body, retries=6):
    for i in range(retries):
        req = urllib.request.Request(f"{CLUSTER}{path}", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {TOKEN}",
                                              "Content-Type": "application/json"})
        try:
            return json.load(urllib.request.urlopen(req))
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and i < retries - 1:
                time.sleep(2 ** i)
                continue
            raise RuntimeError(f"{e.code}: {e.read().decode()[:300]}") from None


def query(csl):
    r = kusto("/v2/rest/query", {"db": DB, "csl": csl})
    tbl = next(f for f in r if f.get("TableKind") == "PrimaryResult")
    cols = [c["ColumnName"] for c in tbl["Columns"]]
    return [dict(zip(cols, row)) for row in tbl["Rows"]]


def ingest(rows):
    for i in range(0, len(rows), CHUNK):
        nd = "\n".join(json.dumps(r) for r in rows[i:i + CHUNK])
        kusto("/v1/rest/mgmt", {"db": DB, "csl":
              f".ingest inline into table WorkflowSteps with "
              f"(format='json', ingestionMappingReference='StepsMap') <|\n{nd}"})


def gh(path, retries=6):
    """GitHub throttles hard under parallel load and the throttle message is not
    stable enough to pattern-match, so retry every failure before giving up.
    Otherwise a throttled job is indistinguishable from an expired one."""
    last = ""
    for i in range(retries):
        p = subprocess.run(["gh", "api", path], capture_output=True, env=ENV, text=True)
        if p.returncode == 0:
            return json.loads(p.stdout)
        last = p.stderr.strip()
        if "404" in last or "Not Found" in last:
            return None
        time.sleep(2 ** i)
    MISS.append(last[:120])
    return None


def fetch(job):
    """check_run_id -> step rows. Returns [] when the job has aged out."""
    d = gh(f"/repositories/{job['repo']}/actions/jobs/{job['check_run_id']}")
    if not d:
        return []
    labels = json.dumps(d.get("labels") or [])
    out = []
    for s in d.get("steps") or []:
        st, ct = s.get("started_at"), s.get("completed_at")
        dur = None
        if st and ct:
            from datetime import datetime
            f = "%Y-%m-%dT%H:%M:%SZ"
            dur = (datetime.strptime(ct, f) - datetime.strptime(st, f)).total_seconds()
        out.append({
            "run_id": job["run_id"], "attempt": job["attempt"],
            "check_run_id": job["check_run_id"], "repo": str(job["repo"]),
            "job_name": d.get("name"), "job_key": job["job_key"],
            "job_conclusion": d.get("conclusion"),
            "runner_name": d.get("runner_name"), "labels": labels,
            "step_number": s.get("number"), "step_name": s.get("name"),
            "step_status": s.get("status"), "step_conclusion": s.get("conclusion"),
            "started_at": st, "completed_at": ct, "duration_seconds": dur,
        })
    return out


def main(limit=400, workers=4):
    done = {r["check_run_id"] for r in query("WorkflowSteps | distinct check_run_id")}
    jobs = [j for j in query(f"""
        {RAW}
        | where eventType == 'workflow_job_completed'
        // skipped/cancelled jobs never populate steps[] -- 45% of events, all wasted calls
        | where tostring(eventData.job_conclusion) !in ('skipped', 'cancelled')
        | project run_id = tolong(eventData.workflow_run_id),
                  attempt = toint(eventData.workflow_run_attempt),
                  check_run_id = tolong(eventData.check_run_id),
                  repo = tolong(eventData.repository_id),
                  job_key = tostring(eventData.job_key),
                  ts = eventTimestamp
        | summarize arg_max(ts, *) by check_run_id
        | top {limit} by ts desc""") if j["check_run_id"] not in done]

    print(f"{len(jobs)} jobs to resolve ({len(done)} already ingested)")
    rows, miss, t0 = [], 0, time.time()
    with ThreadPoolExecutor(workers) as ex:
        for n, r in enumerate(ex.map(fetch, jobs), 1):
            if r:
                rows += r
            else:
                miss += 1
            if n % 50 == 0:
                print(f"  {n}/{len(jobs)}  steps={len(rows)}  missing={miss}")
    if rows:
        ingest(rows)
    if MISS:
        from collections import Counter
        print("  failure reasons:", dict(Counter(MISS).most_common(3)))
    print(f"{len(rows)} steps from {len(jobs) - miss} jobs "
          f"({miss} without steps) in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 400)
