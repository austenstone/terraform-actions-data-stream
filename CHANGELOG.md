# Changelog

## Unreleased

## v0.3.0 (2026-08-05)

Additive and non-breaking. Existing `azure-kusto`, `azure-blob` and `aws-s3` deployments plan
unchanged.

### Added

- `modules/fabric-eventhouse` — a Microsoft Fabric eventhouse and KQL database carrying the same
  table, ingestion mapping, analytics and enrichment layers as `modules/azure-kusto`. An eventhouse
  *is* the Kusto engine, so its `sink_config` output is shape-identical and the `azure_kusto` sink
  takes it with a URI swap and no code change. Unlocks Real-Time Dashboards, Activator alerting,
  OneLake mirroring to Delta and Power BI Direct Lake, and drops the standing ADX cluster cost.
- `examples/fabric-eventhouse`, plus `fabric-eventhouse` as a `deploy.yml` target driven by a new
  `FABRIC_WORKSPACE_ID` repository variable.
- `kql-drift` CI job, guarding the vendored copy of the shared KQL against divergence.

### Changed

- `scripts/apply-kql.sh` needs only `curl`. JSON is built with parameter expansion instead of `jq`,
  and a `KUSTO_TOKEN` environment variable bypasses the Azure CLI, so the module runs on Terraform
  Cloud, Spacelift and bare runners.
- `shellcheck` in CI now covers `modules/*/scripts/*.sh`, not just `scripts/*.sh`.

## v0.2.0 (2026-08-05)

Additive and non-breaking. No materialized view changed, so upgrading from `v0.1.0` does
not reprocess history. `enrichment_subjects` defaults to empty, so an existing deployment
that does not set it plans no new resources.

### Added

- `enrichment_subjects` on the Kusto module. Given a list of GitHub OIDC subjects, it creates a
  second managed identity holding **Viewer + Ingestor** on the database so a scheduled workflow can
  run the enrichment scripts with no stored Azure secret. Empty by default, so existing deployments
  plan unchanged. Output: `enrichment_identity` (`client_id`, `tenant_id`, `subscription_id`).
- `examples/enrichment-workflow.yml` — a working hourly workflow for the above.
- `enrichment_subjects` and an `enrichment_identity` output on `examples/azure-kusto`, so the
  example actually exercises the feature.

### Fixed

- The enrichment scripts dropped `GH_TOKEN` from the environment they passed to `gh`, which is
  correct on a laptop with a keyring but leaves **no credential at all in CI**, where the
  environment variable is the only one there is. The variable is now kept by default; set
  `GH_IGNORE_ENV_TOKEN=1` for the laptop case. This made the scheduled workflow above impossible to
  actually run before now.

### Documented

- `azurerm_kusto_script` replaces on any `script_content` change, which re-executes the whole `.kql`
  file. The README previously called re-applying an edited view "a no-op" — true of the effect in
  Kusto, misleading about what Terraform does. Adding an unrelated variable to an existing
  deployment can therefore plan a script replacement you did not ask for; `-target` around it.

## v0.1.0

First tagged release. Everything before this was `main`.

Provisions a Data Stream destination on Azure Blob, Azure Data Explorer, Azure Event Hubs or
Amazon S3, authenticating with OIDC federated credentials so there is no stored secret. The Kusto
module additionally ships a serving layer over the raw events, three enrichment scripts, and a
seven-page dashboard.

### Upgrading

Pin `ref` to a tag. The Kusto module contains materialized views, and a materialized view cannot be
altered in place — if a future release changes one, upgrading means dropping and rebuilding it,
which reprocesses your history. Releases that require it will say so here. Tracking `main` means
finding out during a `terraform apply`.

### Known preview limitations

The feature itself is in preview and has real gaps — no repository or actor names, no billing data,
no `workflow_run_created` on re-runs, and job completion events for jobs that never ran. These are
catalogued in the README under [Known preview
issues](README.md#known-preview-issues) and filed as issues on this repo.
