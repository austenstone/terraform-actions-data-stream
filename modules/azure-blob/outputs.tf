output "sink_config" {
  description = "Config block for the azure_blob sink. Feed straight to the data stream API."
  value = {
    auth_type = "oidc"
    tenant_id = module.identity.tenant_id
    client_id = module.identity.client_id
    account   = azurerm_storage_account.this.name
    container = azurerm_storage_container.this.name
  }
}

output "subject" {
  description = "OIDC subject claim this identity trusts. Must match the data stream UI exactly."
  value       = module.identity.subject
}
