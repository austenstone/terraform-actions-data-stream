#!/usr/bin/env python3
"""Render the dashboard template and push it to Fabric.

The committed RealTimeDashboard.json is a Terraform template: its dataSources
carry ${cluster_uri} and ${database} placeholders so the module can point a
deployment at any cluster. Uploading that file verbatim produces a dashboard
whose tiles hang on "Loading..." forever -- the client calls new URL() on the
literal placeholder, throws, and its own error handler dies before it can
render the failure. Nothing shows up in the cluster's query log because no
query is ever sent. Always publish through here.

Env: FABRIC_WORKSPACE, FABRIC_ITEM, KUSTO_CLUSTER (or KUSTO_CLUSTER_URI), KUSTO_DATABASE
"""
import base64
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DASH = ROOT / "modules/azure-kusto/dashboard/RealTimeDashboard.json"

# The schema validator is the only thing standing between you and a dashboard
# that writes successfully and then refuses to render. Look for it in the repo
# first, then an explicit override, then the skills checkout it originally
# shipped from. If none exist we warn rather than skip quietly -- a silent
# no-op here is how a broken document reaches the browser.
VALIDATOR = next(
    (p for p in (
        ROOT / "scripts/validate_dashboard.py",
        pathlib.Path(os.environ["DASHBOARD_VALIDATOR"]) if os.environ.get("DASHBOARD_VALIDATOR") else None,
        pathlib.Path.home() / "source/austenstone-notes/.github/skills/fabric-dashboards/scripts/validate_dashboard.py",
    ) if p and p.exists()),
    None,
)


def required(*names):
    for n in names:
        if os.environ.get(n):
            return os.environ[n]
    sys.exit(f"set {names[0]} (this script publishes to *your* Fabric workspace, "
             f"so there is deliberately no default)")


WORKSPACE = required("FABRIC_WORKSPACE")
ITEM = required("FABRIC_ITEM")
CLUSTER = required("KUSTO_CLUSTER", "KUSTO_CLUSTER_URI")
DATABASE = os.environ.get("KUSTO_DATABASE", "ActionsDataStream")


def main():
    raw = DASH.read_text()
    if VALIDATOR:
        if subprocess.run([sys.executable, str(VALIDATOR), str(DASH)]).returncode != 0:
            sys.exit("validation failed, refusing to publish")
    else:
        print("WARNING: no schema validator found, publishing unvalidated. "
              "Set DASHBOARD_VALIDATOR to enable the check.", file=sys.stderr)

    rendered = raw.replace("${cluster_uri}", CLUSTER).replace("${database}", DATABASE)
    doc = json.loads(rendered)
    for ds in doc["dataSources"]:
        if "${" in json.dumps(ds):
            sys.exit(f"unrendered placeholder left in dataSource: {ds}")
        print(f"dataSource {ds['name']}: {ds['clusterUri']}/{ds['database']}")

    body = {"definition": {"parts": [{
        "path": "RealTimeDashboard.json",
        "payload": base64.b64encode(json.dumps(doc).encode()).decode(),
        "payloadType": "InlineBase64",
    }]}}
    out = pathlib.Path("/tmp/publish_body.json")
    out.write_text(json.dumps(body))
    subprocess.run(["fab", "api", f"workspaces/{WORKSPACE}/items/{ITEM}/updateDefinition",
                    "-X", "post", "-i", str(out)], check=True)


if __name__ == "__main__":
    main()
