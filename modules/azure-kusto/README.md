# azure-kusto module

Kusto cluster, database, table + ingestion mapping, and the `Ingestor` role assignment
for the data stream identity. Usage is in the [repo README](../../README.md#azure-data-explorer-kusto);
this covers maintaining the module itself.

## ADX streaming ingestion caches table schema for ~5 minutes

If you add a table to an existing cluster and immediately point a sink at it, `test-connection`
fails:

```
kusto upload failed with status 400: BadRequest_EntityNotFound
```

even though `.show tables`, `.show table X ingestion json mappings` and
`.show table X policy streamingingestion` all confirm everything exists. This is ADX caching the
streaming schema, not a GitHub bug. Wait ~5 minutes after creating a table and retry. Terraform's
`azurerm_kusto_script` runs before the sink exists, so a clean `apply` is usually far enough ahead of
first ingest that you never see this.

## Changing a materialized view needs a manual drop

Editing [`kql/analytics.kql`](kql/analytics.kql) and re-applying **does not
change a materialized view**, because the view definitions use `.create ifnotexists`. Functions are
fine — they use `.create-or-alter`. Views need:

```kusto
.drop materialized-view Jobs          // note: does NOT accept `ifexists`
.create materialized-view with (backfill=true) Jobs on table ActionsEvents { ... }
```

`backfill` is create-only. `.create-or-alter materialized-view with (backfill=true)` works exactly
once and then fails forever with *"Unsupported property in materialized view alter command"*.

Do not read that as "re-applying is free". `script_content` **forces replacement** on
`azurerm_kusto_script`, so any edit to a `.kql` file destroys and recreates the resource, which
**re-executes the entire script**:

```
# module.sink.azurerm_kusto_script.analytics[0] must be replaced
~ script_content = (sensitive value) # forces replacement
```

Nothing changes in Kusto, but every command in the file runs again, so one non-idempotent line
anywhere fails the apply — and a failed `azurerm_kusto_script` still leaves the resource in Azure,
so the next apply needs a `terraform import` before it will even plan. The practical consequence is
that a plan touching a script is never as small as it looks: if you are adding something unrelated
to an existing deployment, `-target` the resources you actually want.
