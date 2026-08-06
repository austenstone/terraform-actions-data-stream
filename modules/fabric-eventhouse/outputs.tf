output "sink_config" {
  description = "Config block for the azure_kusto sink. Feed straight to the data stream API. Identical in shape to the azure-kusto module's -- only the endpoints differ."
  value = {
    auth_type      = "oidc"
    tenant_id      = module.identity.tenant_id
    client_id      = module.identity.client_id
    ingestion_type = var.ingestion_type
    database       = var.database_name
    table          = var.table_name
    mapping_name   = var.mapping_name

    # Both URIs are required even when ingestion_type is streaming. Sending only
    # one makes the API return a 500 rather than a validation error.
    cluster_uri   = fabric_kql_database.this.properties.query_service_uri
    ingestion_uri = fabric_kql_database.this.properties.ingestion_service_uri
  }
}

output "cluster_uri" {
  description = "Query endpoint. Open this in Kusto Web Explorer, or query the database directly in Fabric."
  value       = fabric_kql_database.this.properties.query_service_uri
}

output "subject" {
  description = "OIDC subject claim this identity trusts."
  value       = module.identity.subject
}

output "database_name" {
  description = "Database the stream writes to."
  value       = var.database_name
}

output "eventhouse_id" {
  description = "Fabric item ID of the eventhouse."
  value       = fabric_eventhouse.this.id
}

output "dashboard_json" {
  description = <<-DESC
    A ready-to-import Real-Time Dashboard, pointed at this database. Write it to
    a file and import it in Fabric under Real-Time Intelligence > Real-Time
    Dashboard > New > Import:
      terraform output -raw dashboard_json > dashboard.json
  DESC
  value = templatefile("${path.module}/../azure-kusto/dashboard/RealTimeDashboard.json", {
    cluster_uri = fabric_kql_database.this.properties.query_service_uri
    database    = var.database_name
  })
}

output "ingestor_grant_command" {
  description = <<-DESC
    The management command that grants the stream identity Ingestor, for when
    create_ingestor_grant is false. Run it against the database in Fabric as
    someone with Database Admin.
  DESC
  value       = ".add database ${var.database_name} ingestors ('aadapp=${module.identity.client_id};${module.identity.tenant_id}') 'actions-data-stream'"
}

output "enrichment_identity" {
  description = <<-DESC
    Credentials for the enrichment workflow, or null if enrichment_subjects was
    left empty. Feed these to azure/login in the workflow that runs scripts/.
  DESC
  value = local.enable_enrichment ? {
    client_id       = azurerm_user_assigned_identity.enrichment[0].client_id
    tenant_id       = azurerm_user_assigned_identity.enrichment[0].tenant_id
    subscription_id = data.azurerm_client_config.current.subscription_id
  } : null
}
