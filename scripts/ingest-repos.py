#!/usr/bin/env python3
"""Resolve the stream's numeric ids into names.

Every event carries repository_id and repository_owner_id and no names at all,
so an unenriched dashboard can only offer "repo 1324410793" as a filter. One
REST call per repo fixes that, and the response carries owner.login too, so
orgs come free -- there is no second lookup.

Rows are appended, never replaced, and readers take the newest row per id.
Repos get renamed and transferred between orgs; keeping the history means an
old run still resolves to the name it had, instead of silently retconning.
"""
import json, os, subprocess, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

CLUSTER = os.environ["KUSTO_CLUSTER"].rstrip("/")
DB, CHUNK = os.environ.get("KUSTO_DATABASE", "ActionsDataStream"), 5000
# Must match var.table_name if you changed it from the module default.
RAW = os.environ.get("KUSTO_TABLE", "ActionsEvents")
TOKEN = os.environ.get("KUSTO_TOKEN") or subprocess.run(
    ["az", "account", "get-access-token", "--resource", CLUSTER,
     "--query", "accessToken", "-o", "tsv"],
    capture_output=True, text=True, check=True).stdout.strip()
# gh resolves credentials from GH_TOKEN/GITHUB_TOKEN before its keyring, so in
# CI the environment variable is the only credential there is and must be kept.
# Locally that variable is sometimes a low-scope token shadowing a better one in
# the keyring; set GH_IGNORE_ENV_TOKEN=1 to drop it and fall back to the keyring.
def _gh_env():
    env = dict(os.environ)
    if os.environ.get("GH_IGNORE_ENV_TOKEN") == "1":
        env.pop("GH_TOKEN", None)
    return env

ENV = _gh_env()


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
              f".ingest inline into table Repos with "
              f"(format='json', ingestionMappingReference='ReposMap') <|\n{nd}"})


def gh(path, retries=6):
    for i in range(retries):
        p = subprocess.run(["gh", "api", path], capture_output=True, env=ENV, text=True)
        if p.returncode == 0:
            return json.loads(p.stdout)
        if "404" in p.stderr or "Not Found" in p.stderr:
            return None
        time.sleep(2 ** i)
    return None


def fetch(repo_id):
    """A deleted repo 404s forever. Record it so we stop retrying, and so the
    dashboard shows a stable label instead of dropping the runs entirely."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = gh(f"/repositories/{repo_id}")
    if not d:
        return {"repository_id": repo_id, "owner_id": None, "owner": None,
                "name": None, "full_name": f"repo/{repo_id}", "visibility": None,
                "archived": None, "resolved": False, "resolved_at": now}
    owner = d.get("owner") or {}
    return {"repository_id": repo_id, "owner_id": owner.get("id"),
            "owner": owner.get("login"), "name": d.get("name"),
            "full_name": d.get("full_name"), "visibility": d.get("visibility"),
            "archived": d.get("archived"), "resolved": True, "resolved_at": now}


def main(workers=8):
    known = {r["repository_id"] for r in query("Repos | distinct repository_id")}
    # eventData is dynamic for most rows but arrives as a JSON string for some,
    # and dotted access silently yields null on those. Round-tripping through
    # todynamic(tostring(...)) handles both and is what the KQL functions do.
    ids = [r["repository_id"] for r in query(f"""
        {RAW}
        | extend repository_id = tolong(todynamic(tostring(eventData)).repository_id)
        | where isnotnull(repository_id) and repository_id > 0
        | distinct repository_id""")
        if r["repository_id"] not in known]

    print(f"{len(ids)} repos to resolve ({len(known)} already known)")
    if not ids:
        return
    rows, t0 = [], time.time()
    with ThreadPoolExecutor(workers) as ex:
        for n, r in enumerate(ex.map(fetch, ids), 1):
            rows.append(r)
            if n % 100 == 0:
                print(f"  {n}/{len(ids)}")
    ingest(rows)
    gone = sum(1 for r in rows if not r["resolved"])
    print(f"{len(rows) - gone} resolved, {gone} deleted/inaccessible "
          f"in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
