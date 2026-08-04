output "sink_config" {
  description = "Config block for the azure_kusto sink. Feed straight to the data stream API."
  value = {
    auth_type      = "oidc"
    tenant_id      = module.identity.tenant_id
    client_id      = module.identity.client_id
    ingestion_type = var.ingestion_type
    database       = azurerm_kusto_database.this.name
    table          = var.table_name
    mapping_name   = var.mapping_name

    # Both URIs are required even when ingestion_type is streaming. Sending only
    # one makes the API return a 500 rather than a validation error.
    cluster_uri   = azurerm_kusto_cluster.this.uri
    ingestion_uri = azurerm_kusto_cluster.this.data_ingestion_uri
  }
}

output "cluster_uri" {
  description = "Query endpoint. Open this in Kusto Web Explorer."
  value       = azurerm_kusto_cluster.this.uri
}

output "subject" {
  description = "OIDC subject claim this identity trusts."
  value       = module.identity.subject
}

output "database_name" {
  description = "Database the stream writes to."
  value       = azurerm_kusto_database.this.name
}

output "dashboard_json" {
  description = <<-DESC
    A ready-to-import Kusto dashboard, pointed at this cluster and database.
    Write it to a file and import it in Kusto Web Explorer or Fabric:
      terraform output -raw dashboard_json > dashboard.json
  DESC
  value = templatefile("${path.module}/dashboard/RealTimeDashboard.json", {
    cluster_uri = azurerm_kusto_cluster.this.uri
    database    = azurerm_kusto_database.this.name
  })
}
