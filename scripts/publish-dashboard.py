#!/usr/bin/env python3
"""Render the dashboard template and push it to Fabric.

The committed RealTimeDashboard.json is a Terraform template: its dataSources
carry ${cluster_uri} and ${database} placeholders so the module can point a
deployment at any cluster. Uploading that file verbatim produces a dashboard
whose tiles hang on "Loading..." forever -- the client calls new URL() on the
literal placeholder, throws, and its own error handler dies before it can
render the failure. Nothing shows up in the cluster's query log because no
query is ever sent. Always publish through here.

Env: FABRIC_WORKSPACE, FABRIC_ITEM, KUSTO_CLUSTER_URI, KUSTO_DATABASE
"""
import base64
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DASH = ROOT / "modules/azure-kusto/dashboard/RealTimeDashboard.json"
VALIDATOR = pathlib.Path.home() / "source/austenstone-notes/.github/skills/fabric-dashboards/scripts/validate_dashboard.py"

WORKSPACE = os.environ.get("FABRIC_WORKSPACE", "d04c130d-dbea-4cde-906a-f1ba2d4c80b0")
ITEM = os.environ.get("FABRIC_ITEM", "0001e273-a7ae-4e4d-9742-53ca051a2158")
CLUSTER = os.environ.get("KUSTO_CLUSTER_URI", "https://austenadskusto.eastus2.kusto.windows.net")
DATABASE = os.environ.get("KUSTO_DATABASE", "ActionsDataStream")


def main():
    raw = DASH.read_text()
    if VALIDATOR.exists():
        if subprocess.run([sys.executable, str(VALIDATOR), str(DASH)]).returncode != 0:
            sys.exit("validation failed, refusing to publish")

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
