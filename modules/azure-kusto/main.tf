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

resource "azurerm_kusto_cluster" "this" {
  name                = var.cluster_name
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags

  sku {
    name     = var.sku_name
    capacity = var.sku_capacity
  }

  streaming_ingestion_enabled = var.ingestion_type == "streaming"
}

resource "azurerm_kusto_database" "this" {
  name                = var.database_name
  resource_group_name = var.resource_group_name
  location            = var.location
  cluster_name        = azurerm_kusto_cluster.this.name
  hot_cache_period    = var.hot_cache_period
  soft_delete_period  = var.soft_delete_period
}

# The script below needs Admin on the database. Azure grants that automatically
# to whoever creates the database, so this is off by default and exists only for
# the case where you are adopting a cluster someone else built. Creating it when
# the assignment already exists fails with a 400.
resource "azurerm_kusto_database_principal_assignment" "deployer" {
  count = var.create_deployer_admin ? 1 : 0

  name                = "terraform-deployer"
  resource_group_name = var.resource_group_name
  cluster_name        = azurerm_kusto_cluster.this.name
  database_name       = azurerm_kusto_database.this.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  principal_id        = data.azurerm_client_config.current.object_id
  principal_type      = "User"
  role                = "Admin"
}

# Nobody but the deployer can read this database. There is no tenant-wide default,
# and Kusto rejects Microsoft 365 / Unified groups ("All Company" and friends) with
# "Requested entity is not valid and could not be retrieved from AAD" — it only
# accepts security-enabled groups. Sharing a dashboard does NOT grant DB access:
# Fabric item permissions and Kusto database ACLs are independent, so a teammate
# who can open the dashboard still gets a bare "Access denied" naming only their
# principal GUID, with no hint about which system refused them.
#
# Set viewer_group_object_id to a security group and manage membership there.
resource "azurerm_kusto_database_principal_assignment" "viewers" {
  count = var.viewer_group_object_id == null ? 0 : 1

  name                = "viewers"
  resource_group_name = var.resource_group_name
  cluster_name        = azurerm_kusto_cluster.this.name
  database_name       = azurerm_kusto_database.this.name
  tenant_id           = data.azurerm_client_config.current.tenant_id
  principal_id        = var.viewer_group_object_id
  principal_type      = "Group"
  role                = "Viewer"
}

resource "azurerm_kusto_database_principal_assignment" "ingestor" {
  name                = "actions-data-stream"
  resource_group_name = var.resource_group_name
  cluster_name        = azurerm_kusto_cluster.this.name
  database_name       = azurerm_kusto_database.this.name
  tenant_id           = module.identity.tenant_id
  principal_id        = module.identity.principal_id
  principal_type      = "App"
  role                = "Ingestor"
}

locals {
  streaming_policy = var.ingestion_type == "streaming" ? ".alter table ${var.table_name} policy streamingingestion enable" : ""

  # Columns mirror the data stream envelope. eventData stays dynamic because its
  # shape differs per eventType.
  script = <<-KQL
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

    ${local.streaming_policy}
  KQL
}

resource "azurerm_kusto_script" "schema" {
  name                               = "actions-data-stream-schema"
  database_id                        = azurerm_kusto_database.this.id
  script_content                     = local.script
  continue_on_errors_enabled         = false
  force_an_update_when_value_changed = sha256(local.script)

  depends_on = [azurerm_kusto_database_principal_assignment.deployer]
}

# Silver/gold/OTel layer. Split from the schema script so the raw table lands
# even if you don't want the analytics, and so the KQL stays readable on its own.
locals {
  analytics = templatefile("${path.module}/kql/analytics.kql", {
    table_name = var.table_name
  })
}

resource "azurerm_kusto_script" "analytics" {
  count = var.create_analytics ? 1 : 0

  name                               = "actions-data-stream-analytics"
  database_id                        = azurerm_kusto_database.this.id
  script_content                     = local.analytics
  continue_on_errors_enabled         = false
  force_an_update_when_value_changed = sha256(local.analytics)

  depends_on = [azurerm_kusto_script.schema]
}

# Enrichment layer. Schema only -- the step and log tables are populated by the
# ingesters in scripts/, which pull from the REST API using ids the stream emits.
# Separate script so you can take the analytics without the enrichment.
locals {
  enrichment = file("${path.module}/kql/enrichment.kql")
}

resource "azurerm_kusto_script" "enrichment" {
  count = var.create_enrichment ? 1 : 0

  name                               = "actions-data-stream-enrichment"
  database_id                        = azurerm_kusto_database.this.id
  script_content                     = local.enrichment
  continue_on_errors_enabled         = false
  force_an_update_when_value_changed = sha256(local.enrichment)

  depends_on = [azurerm_kusto_script.analytics]
}
