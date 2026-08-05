import json, subprocess, sys, uuid
NS = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
d = json.load(open("modules/azure-kusto/dashboard/RealTimeDashboard.json"))
names = ["opt_org","opt_repo","opt_wf","opt_job","crumb","shape","repos",
         "workflows","jobs","steps","runs","timeline","stranded"]
ids = {str(uuid.uuid5(NS, f"explorer:query:{n}")): n for n in names}
qs = {ids[q["id"]]: q["text"] for q in d["queries"] if q["id"] in ids}

scenario = sys.argv[1] if len(sys.argv) > 1 else "all"
if scenario == "all":
    binds = {"_org": '""', "_repo": '""', "_wf": '""', "_job": '""', "_run": '""'}
else:
    binds = {"_org": '"octodemo"', "_repo": '"octodemo/lchainjs"',
             "_wf": '""', "_job": '""', "_run": '""'}
head = ("let _startTime = ago(7d);\nlet _endTime = now();\n"
        + "".join(f"let {k} = {v};\n" for k, v in binds.items()))

fails = 0
for n in names:
    r = subprocess.run(["/tmp/run.sh", head + qs[n]], capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    bad = r.returncode != 0 or "Error" in out or "error" in out.lower()[:2000]
    rows = len([l for l in out.strip().split("\n") if l.strip()])
    print(f"{'FAIL' if bad else 'ok  '} {n:12s} rows≈{rows}")
    if bad:
        fails += 1
        print("      " + out.strip().replace("\n", "\n      ")[:600])
print(f"\n{scenario}: {len(names)-fails}/{len(names)} ok")
