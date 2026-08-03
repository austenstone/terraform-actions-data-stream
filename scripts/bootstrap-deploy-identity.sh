#!/usr/bin/env bash
# One-time bootstrap for the deploy workflow's Azure identity.
#
# The deploy workflow authenticates with OIDC, which means something has to
# create the identity it authenticates as, and that something cannot be the
# workflow. Run this once, from a shell with Owner or User Access Administrator
# on the subscription.
#
# Note this is a *different* federated credential from the one the data stream
# modules create. That one lets GitHub's data stream service write to your sink
# (subject actions-data-stream:organization/<id>). This one lets a workflow in
# your repository deploy infrastructure (subject repo:<owner>/<repo>:...).
#
# Usage:
#   scripts/bootstrap-deploy-identity.sh <subscription_id> <owner/repo> [resource_group] [location]
#
# Example:
#   scripts/bootstrap-deploy-identity.sh 00000000-0000-0000-0000-000000000000 acme/data-stream-infra

set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <subscription_id> <owner/repo> [resource_group] [location]" >&2
  exit 64
fi

subscription_id=$1
repo=$2
resource_group=${3:-terraform-deploy-identity}
location=${4:-eastus}
identity_name="github-actions-deploy"

command -v az >/dev/null || {
  echo "error: az not found" >&2
  exit 69
}

if [[ $repo != */* ]]; then
  echo "error: repo must be in owner/repo form, got '$repo'" >&2
  exit 64
fi

az account set --subscription "$subscription_id"

echo "Creating $resource_group in $location..."
az group create --name "$resource_group" --location "$location" --output none

echo "Creating managed identity $identity_name..."
az identity create \
  --name "$identity_name" --resource-group "$resource_group" --location "$location" \
  --output none

client_id=$(az identity show --name "$identity_name" --resource-group "$resource_group" --query clientId -o tsv)
principal_id=$(az identity show --name "$identity_name" --resource-group "$resource_group" --query principalId -o tsv)
tenant_id=$(az account show --query tenantId -o tsv)

# One credential per environment the workflow targets. Azure matches the subject
# exactly, so a missing one fails with AADSTS70021 at dispatch time rather than here.
for env_name in plan apply destroy; do
  echo "Adding federated credential for environment $env_name..."
  az identity federated-credential create \
    --name "github-$env_name" \
    --identity-name "$identity_name" \
    --resource-group "$resource_group" \
    --issuer "https://token.actions.githubusercontent.com" \
    --subject "repo:${repo}:environment:${env_name}" \
    --audiences "api://AzureADTokenExchange" \
    --output none
done

echo "Granting Contributor on the subscription..."
# Contributor is enough for everything except Azure RBAC role assignments. The
# blob module needs one of those, so grant User Access Administrator too if you
# plan to deploy that path unattended.
az role assignment create \
  --assignee-object-id "$principal_id" \
  --assignee-principal-type ServicePrincipal \
  --role Contributor \
  --scope "/subscriptions/$subscription_id" \
  --output none

cat <<EOF

Done. Set these as repository variables on $repo:

  AZURE_CLIENT_ID        $client_id
  AZURE_TENANT_ID        $tenant_id
  AZURE_SUBSCRIPTION_ID  $subscription_id

  gh variable set AZURE_CLIENT_ID --body "$client_id" --repo "$repo"
  gh variable set AZURE_TENANT_ID --body "$tenant_id" --repo "$repo"
  gh variable set AZURE_SUBSCRIPTION_ID --body "$subscription_id" --repo "$repo"

The identity has Contributor, which cannot create Azure RBAC role assignments.
The blob module needs one, so either grant User Access Administrator as well or
set create_role_assignment = false and have someone with rights run the command
the module outputs.
EOF
