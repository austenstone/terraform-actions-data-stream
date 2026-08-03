output "sink_config" {
  description = "Config block for the azure_blob sink."
  value       = module.sink.sink_config
}

output "subject" {
  description = "OIDC subject claim this identity trusts."
  value       = module.sink.subject
}

output "test_connection_command" {
  description = "Validate the sink against GitHub before creating it. Creates nothing."
  value = format(
    "gh api -X POST /orgs/YOUR_ORG/actions/data-stream/sinks/test-connection --input - <<'EOF'\n%s\nEOF",
    jsonencode({
      name      = "azure-blob"
      sink_type = "azure_blob"
      config    = module.sink.sink_config
    })
  )
}
