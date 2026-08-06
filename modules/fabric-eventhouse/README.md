# fabric-eventhouse module

Fabric eventhouse, KQL database, table + ingestion mapping, and the `Ingestor` grant for the
data stream identity. Usage is in the [repo README](../../README.md#fabric-eventhouse); this covers
maintaining the module itself.

An eventhouse *is* the Kusto engine, so this module and [`../azure-kusto`](../azure-kusto) emit an
identical `sink_config` — only the endpoints differ. The KQL is shared rather than forked: both the
[analytics](../azure-kusto/kql/analytics.kql) and [enrichment](../azure-kusto/kql/enrichment.kql)
layers and the [dashboard](../azure-kusto/dashboard/RealTimeDashboard.json) are read across from
`azure-kusto`.

## Why KQL goes through a shell script

`configuration` and `definition` are mutually exclusive on `fabric_kql_database`, and
`configuration` is the one that attaches the database to its parent eventhouse. So the declarative
`DatabaseSchema.kql` route is unavailable.

The grants force the issue anyway. The provider has no equivalent of
`azurerm_kusto_database_principal_assignment`, and `Ingestor` is only assignable with a management
command. Fabric workspace Admin, Member and Contributor *do* all inherit Kusto Admin on every
database in the workspace, so `fabric_workspace_role_assignment` would be one clean resource — and
the wrong trade. It hands a write-only process Admin over the entire workspace.

Since an out-of-band management call is unavoidable for the grants, the schema rides along in the
same place. [`scripts/apply-kql.sh`](scripts/apply-kql.sh) POSTs to `/v1/rest/mgmt`.

Set `create_ingestor_grant = false` if you deploy as someone below Database Admin, then hand the
`ingestor_grant_command` output to someone who has it.

## Running where the Azure CLI is not installed

`curl` is the script's only hard requirement. JSON is assembled with parameter expansion rather than
`jq`, and the Azure CLI is used only to mint a token when it happens to be on `PATH`.

Terraform Cloud, Spacelift and bare runners have neither CLI. Export a bearer token scoped to the
eventhouse and the CLI path is skipped entirely:

```bash
export KUSTO_TOKEN='eyJ0...'
terraform apply
```

`local-exec` inherits the parent environment, so nothing needs to be threaded through a variable.

## The KQL is vendored, not shared

[`kql/`](kql) is a byte-identical copy of `modules/azure-kusto/kql`. Reading it across modules would
work through a `git::` source — Terraform clones the whole repository — but breaks the moment
somebody copies this directory out on its own. The `kql-drift` job in
[`validate.yml`](../../.github/workflows/validate.yml) fails the build if the copies diverge:

```bash
cp modules/azure-kusto/kql/analytics.kql modules/fabric-eventhouse/kql/analytics.kql
```

## ThrowOnErrors is not optional

Batches go through `.execute database script`, which **reports success even when every statement
inside it failed**. `ContinueOnErrors = false` does not fix this — the two properties are mutually
exclusive and you need `ThrowOnErrors = true` specifically. The script always sets it. Do not
remove it to "simplify".

The command is also non-transactional with no rollback, so every command it carries is written in an
idempotent form (`.create-merge`, `.create-or-alter`, `.create ifnotexists`).

## Streaming ingestion policy is an unknown

ADX gates streaming on a cluster flag plus a per-table policy. Fabric documents
[the streaming REST endpoint as supported](https://learn.microsoft.com/en-us/kusto/api/rest/streaming-ingest?view=microsoft-fabric),
but says nothing either way about the policy, and an eventhouse exposes no cluster-level equivalent
of `EnableStreamingIngest`.

So the module always emits `.alter table <T> policy streamingingestion enable` when
`ingestion_type = "streaming"`. Required and satisfied, or not required and a no-op. If streaming
still fails on a fresh deployment, set `ingestion_type = "queued"` — different endpoint, no policy
dependency.

## Streaming ingestion caches table schema for ~5 minutes

Same engine, same trap as ADX. Point a sink at a table you just created and `test-connection`
fails:

```
kusto upload failed with status 400: BadRequest_EntityNotFound
```

even though `.show tables` and `.show table X ingestion json mappings` both confirm it exists. Wait
~5 minutes after `apply` before configuring the sink. A clean apply is usually far enough ahead of
first ingest that you never see it.

## Changing a materialized view needs a manual drop

Inherited from the shared analytics KQL. The view definitions use `.create ifnotexists`, so editing
[`../azure-kusto/kql/analytics.kql`](../azure-kusto/kql/analytics.kql) and re-applying **does not
change a materialized view**. Functions are fine — they use `.create-or-alter`. Views need:

```kusto
.drop materialized-view Jobs          // note: does NOT accept `ifexists`
.create materialized-view with (backfill=true) Jobs on table ActionsEvents { ... }
```

`backfill` is create-only. `.create-or-alter materialized-view with (backfill=true)` works exactly
once and then fails forever with *"Unsupported property in materialized view alter command"*.

Editing any `.kql` file changes the `terraform_data` trigger, which re-runs the whole file. Nothing
changes in Kusto, but one non-idempotent line anywhere fails the apply.

## Fabric item permissions are not Kusto ACLs

Sharing the eventhouse in the Fabric portal does not grant query access to the database. The failure
surfaces as a bare `Access denied` naming only a principal GUID. Use `viewer_group_object_id` to
close the gap explicitly — and note Kusto rejects Microsoft 365 / Unified groups, so the group has
to be security-enabled.

## Changing minimum_consumption_units replaces the eventhouse

Which drops the database and everything in it. The default of `0` lets the eventhouse suspend when
idle, at the cost of a 5-10 second cold start on the first query after a quiet period.
