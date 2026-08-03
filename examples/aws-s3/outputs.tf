output "sink_config" {
  description = "Config block for the s3 sink."
  value       = module.sink.sink_config
}

output "subject" {
  description = "OIDC subject claim this role trusts."
  value       = module.sink.subject
}
