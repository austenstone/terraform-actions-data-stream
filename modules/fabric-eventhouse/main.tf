data "azurerm_client_config" "current" {}

module "identity" {
  source = "../azure-identity"

  name                = var.identity_name
  resource_group_name = var.resource_group_name
  location            = var.location
  github_owner_id     = var.github_owner_id
  github_owner_type   = var.github_owner_type
  tags                = var.tags
}

# A Fabric eventhouse is the Kusto engine. Same KQL, same management commands,
# same ingestion REST API, same Entra auth -- and it exposes the same two URIs
# the azure_kusto sink takes. So this module and modules/azure-kusto produce an
# identical sink_config; only the endpoints differ.
#
# Unlike an ADX cluster, an eventhouse suspends when idle and bills against
# shared Fabric capacity, and it brings Real-Time Dashboards, Activator alerting
# and OneLake mirroring with it.
resource "fabric_eventhouse" "this" {
  display_name = var.eventhouse_name
  workspace_id = var.workspace_id

  configuration = {
    minimum_consumption_units = var.minimum_consumption_units
  }
}

# `configuration` and `definition` are mutually exclusive on this resource.
# `configuration` is what attaches the database to its parent eventhouse, so the
# declarative DatabaseSchema.kql route is off the table and the schema goes
# through the management endpoint below instead.
resource "fabric_kql_database" "this" {
  display_name = var.database_name
  workspace_id = var.workspace_id

  configuration = {
    database_type = "ReadWrite"
    eventhouse_id = fabric_eventhouse.this.id
  }
}

locals {
  # ADX gates streaming ingestion on a cluster flag plus a per-table policy.
  # Fabric documents neither -- the streaming REST endpoint is listed as
  # applying to Fabric, but nothing says whether the policy is required. So it
  # is always applied when streaming is selected: required and satisfied, or not
  # required and a no-op.
  streaming_policy = var.ingestion_type == "streaming" ? ".alter table ${var.table_name} policy streamingingestion enable" : ""

  # Columns mirror the data stream envelope. eventData stays dynamic because its
  # shape differs per eventType.
  schema = <<-KQL
    .create-merge table ${var.table_name} (
        eventUuid: string,
        eventType: string,
        eventTimestamp: datetime,
        enterpriseId: long,
        organizationId: long,
        eventData: dynamic
    )

    .create-or-alter table ${var.table_name} ingestion json mapping "${var.mapping_name}"
    '['
    '    { "column": "eventUuid", "Properties": {"Path": "$.eventUuid"} },'
    '    { "column": "eventType", "Properties": {"Path": "$.eventType"} },'
    '    { "column": "eventTimestamp", "Properties": {"Path": "$.eventTimestamp"} },'
    '    { "column": "enterpriseId", "Properties": {"Path": "$.enterpriseId"} },'
    '    { "column": "organizationId", "Properties": {"Path": "$.organizationId"} },'
    '    { "column": "eventData", "Properties": {"Path": "$.eventData"} }'
    ']'

    .alter database ${var.database_name} policy retention softdelete = ${var.soft_delete_period} recoverability = enabled

    .alter database ${var.database_name} policy caching hot = ${var.hot_cache_period}

    ${local.streaming_policy}
  KQL

  # The analytics and enrichment layers are engine-portable and identical to
  # modules/azure-kusto/kql. They are vendored rather than read across the module
  # boundary: file() paths resolve at plan time with no dependency tracking, so
  # reaching outside the module fails silently instead of loudly. CI fails if the
  # two copies drift.
  analytics = templatefile("${path.module}/kql/analytics.kql", {
    table_name = var.table_name
  })

  enrichment = file("${path.module}/kql/enrichment.kql")

  apply_kql = "${path.module}/scripts/apply-kql.sh"
}

resource "terraform_data" "schema" {
  triggers_replace = [fabric_kql_database.this.id, local.schema]

  provisioner "local-exec" {
    command = local.apply_kql

    environment = {
      KUSTO_URI      = fabric_kql_database.this.properties.query_service_uri
      KUSTO_DATABASE = var.database_name
      KQL_SCRIPT     = local.schema
    }
  }
}

resource "terraform_data" "analytics" {
  count = var.create_analytics ? 1 : 0

  triggers_replace = [fabric_kql_database.this.id, local.analytics]

  provisioner "local-exec" {
    command = local.apply_kql

    environment = {
      KUSTO_URI      = fabric_kql_database.this.properties.query_service_uri
      KUSTO_DATABASE = var.database_name
      KQL_SCRIPT     = local.analytics
    }
  }

  depends_on = [terraform_data.schema]
}

resource "terraform_data" "enrichment" {
  count = var.create_enrichment ? 1 : 0

  triggers_replace = [fabric_kql_database.this.id, local.enrichment]

  provisioner "local-exec" {
    command = local.apply_kql

    environment = {
      KUSTO_URI      = fabric_kql_database.this.properties.query_service_uri
      KUSTO_DATABASE = var.database_name
      KQL_SCRIPT     = local.enrichment
    }
  }

  depends_on = [terraform_data.analytics]
}

# ---------------------------------------------------------------------- grants
#
# The microsoft/fabric provider has no equivalent of
# azurerm_kusto_database_principal_assignment. Fabric workspace Admin, Member and
# Contributor all inherit Kusto Admin on every database in the workspace, but
# Ingestor and Viewer can only be assigned with management commands -- so these
# go through the same endpoint as the schema.
#
# Granting the stream a workspace role instead would be one clean Terraform
# resource, and is the wrong trade: it would give a write-only process Admin over
# every database in the workspace.

locals {
  enable_enrichment = length(var.enrichment_subjects) > 0

  grants = compact([
    var.create_ingestor_grant
    ? ".add database ${var.database_name} ingestors ('aadapp=${module.identity.client_id};${module.identity.tenant_id}') 'actions-data-stream'"
    : "",

    var.viewer_group_object_id == null
    ? ""
    : ".add database ${var.database_name} viewers ('aadgroup=${var.viewer_group_object_id};${data.azurerm_client_config.current.tenant_id}') 'viewers'",

    # Two roles, not Admin. The enrichment scripts query the raw table and ingest
    # into tables this module already created, so they never create or drop one.
    local.enable_enrichment
    ? ".add database ${var.database_name} viewers ('aadapp=${azurerm_user_assigned_identity.enrichment[0].client_id};${azurerm_user_assigned_identity.enrichment[0].tenant_id}') 'enrichment'"
    : "",

    local.enable_enrichment
    ? ".add database ${var.database_name} ingestors ('aadapp=${azurerm_user_assigned_identity.enrichment[0].client_id};${azurerm_user_assigned_identity.enrichment[0].tenant_id}') 'enrichment'"
    : "",
  ])
}

resource "terraform_data" "grants" {
  count = length(local.grants) > 0 ? 1 : 0

  triggers_replace = [fabric_kql_database.this.id, join("\n", local.grants)]

  provisioner "local-exec" {
    command = local.apply_kql

    environment = {
      KUSTO_URI      = fabric_kql_database.this.properties.query_service_uri
      KUSTO_DATABASE = var.database_name
      KQL_SCRIPT     = join("\n\n", local.grants)
    }
  }

  depends_on = [terraform_data.schema]
}

# ------------------------------------------------------------------ enrichment
#
# The scripts in scripts/ fill the gaps the stream leaves: repository and owner
# names, per-step rows, and log bodies. They need to read the raw events and
# write the four enrichment tables, so they get their own identity. The stream's
# identity stays write-only -- a process that only ever appends should not be
# able to read the whole history back out.

resource "azurerm_user_assigned_identity" "enrichment" {
  count = local.enable_enrichment ? 1 : 0

  name                = "${var.identity_name}-enrichment"
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags
}

# Federated credential names must be unique per identity and are not meaningful
# to the token exchange, so they are indexed rather than derived from the
# subject -- subjects contain slashes and colons, which are not legal in a
# resource name.
resource "azurerm_federated_identity_credential" "enrichment" {
  count = length(var.enrichment_subjects)

  name                      = "enrichment-${count.index}"
  user_assigned_identity_id = azurerm_user_assigned_identity.enrichment[0].id
  audience                  = ["api://AzureADTokenExchange"]
  issuer                    = "https://token.actions.githubusercontent.com"
  subject                   = var.enrichment_subjects[count.index]
}
