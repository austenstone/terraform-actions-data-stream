#!/usr/bin/env bash
#
# Applies KQL to a Fabric Eventhouse KQL Database over the Kusto management
# endpoint.
#
# Terraform cannot do this natively. The microsoft/fabric provider has no
# kql_database_principal_assignment resource, and its `definition` block is
# mutually exclusive with the `configuration` block that attaches a database to
# its parent eventhouse. So the schema and the grants both come through here.
#
# Reads from the environment so multi-line KQL never has to survive shell
# quoting:
#
#   KUSTO_URI       query URI, e.g. https://trd-xxxx.z5.kusto.fabric.microsoft.com
#   KUSTO_DATABASE  database name
#   KQL_SCRIPT      one or more management commands
#   KUSTO_TOKEN     optional bearer token; skips the Azure CLI entirely
#
# curl is the only hard dependency, so this runs on Terraform Cloud, Spacelift
# and bare runners as long as KUSTO_TOKEN is supplied. Without it the script
# falls back to the caller's Azure CLI login. Either principal needs Database
# Admin, which Fabric workspace Admin, Member and Contributor all inherit.
set -euo pipefail

: "${KUSTO_URI:?KUSTO_URI is required}"
: "${KUSTO_DATABASE:?KUSTO_DATABASE is required}"
: "${KQL_SCRIPT:?KQL_SCRIPT is required}"

token="${KUSTO_TOKEN:-}"
if [[ -z "${token}" ]]; then
  if ! command -v az >/dev/null 2>&1; then
    echo "Need either the KUSTO_TOKEN environment variable or the Azure CLI on PATH." >&2
    echo "Managed Terraform platforms should set KUSTO_TOKEN to a bearer token" >&2
    echo "scoped to ${KUSTO_URI}/.default." >&2
    exit 1
  fi
  token="$(az account get-access-token --resource "${KUSTO_URI}" --query accessToken -o tsv)"
fi

# Escapes a string into a JSON string body. Avoids a jq dependency, which is not
# present on every Terraform runner. KQL never contains control characters
# beyond the three handled here.
json_escape() {
  local s=$1
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\r'/\\r}
  s=${s//$'\t'/\\t}
  s=${s//$'\n'/\\n}
  printf '%s' "$s"
}

# The endpoint runs exactly one command per call, so batches go through
# `.execute database script`. ThrowOnErrors is not optional: without it the
# outer command reports success even when every statement inside it failed.
csl=".execute database script with (ThrowOnErrors=true) <|
${KQL_SCRIPT}"

body="{\"db\":\"$(json_escape "${KUSTO_DATABASE}")\",\"csl\":\"$(json_escape "${csl}")\"}"

if ! response="$(
  curl -sS --fail-with-body \
    -X POST "${KUSTO_URI}/v1/rest/mgmt" \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json" \
    --data-binary "${body}"
)"; then
  echo "KQL apply failed against ${KUSTO_DATABASE}:" >&2
  echo "${response}" >&2
  exit 1
fi

echo "applied ${KUSTO_DATABASE}"
