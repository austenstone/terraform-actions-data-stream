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
