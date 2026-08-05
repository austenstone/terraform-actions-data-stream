# Changelog

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
