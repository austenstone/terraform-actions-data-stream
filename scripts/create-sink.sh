#!/usr/bin/env bash
# Create an Actions Data Stream sink from Terraform outputs.
#
# Runs test-connection first, which performs the real OIDC token exchange and
# fails loudly with the provider's own error. Nothing is created unless it passes.
#
# Usage:
#   scripts/create-sink.sh <org> <sink_type> <name> [terraform_dir]
#
# Example:
#   scripts/create-sink.sh octodemo azure_blob my-blob-sink examples/azure-blob

set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: $0 <org> <sink_type> <name> [terraform_dir]" >&2
  exit 64
fi

org=$1
sink_type=$2
name=$3
tf_dir=${4:-.}

for cmd in gh jq terraform; do
  command -v "$cmd" >/dev/null || {
    echo "error: $cmd not found" >&2
    exit 69
  }
done

config=$(terraform -chdir="$tf_dir" output -json sink_config)
if [[ -z $config || $config == "null" ]]; then
  echo "error: no sink_config output in $tf_dir. Has terraform apply run?" >&2
  exit 65
fi

payload=$(jq -n \
  --arg name "$name" \
  --arg sink_type "$sink_type" \
  --argjson config "$config" \
  '{name: $name, sink_type: $sink_type, config: $config}')

echo "Testing connection..."
result=$(gh api -X POST "/orgs/$org/actions/data-stream/sinks/test-connection" --input - <<<"$payload")

if [[ $(jq -r '.success' <<<"$result") != "true" ]]; then
  echo "Connection test failed." >&2
  jq -r '"  status:  \(.status_code // "n/a")\n  error:   \(.error_message // "none")\n  latency: \(.latency_ms // "n/a")ms"' <<<"$result" >&2
  echo >&2
  echo "Role assignments can take a few minutes to propagate. If the identity" >&2
  echo "authenticated but the write was rejected, wait and retry before changing config." >&2
  exit 1
fi

echo "Connection OK. Creating sink..."
gh api -X POST "/orgs/$org/actions/data-stream/sinks" --input - <<<"$payload" |
  jq -r '"Created sink \(.id) (\(.name)) status=\(.status)"'
