output "sink_config" {
  description = "Config block for the azure_kusto sink."
  value       = module.sink.sink_config
}

output "cluster_uri" {
  description = "Query endpoint. Open this in Kusto Web Explorer."
  value       = module.sink.cluster_uri
}

output "subject" {
  description = "OIDC subject claim this identity trusts."
  value       = module.sink.subject
}

output "database_name" {
  description = "Database the stream writes to."
  value       = module.sink.database_name
}

output "dashboard_json" {
  description = "Importable Kusto dashboard. terraform output -raw dashboard_json > dashboard.json"
  value       = module.sink.dashboard_json
}
