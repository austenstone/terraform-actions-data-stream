output "client_id" {
  description = "Client ID for the sink config."
  value       = azurerm_user_assigned_identity.this.client_id
}

output "tenant_id" {
  description = "Tenant ID for the sink config."
  value       = azurerm_user_assigned_identity.this.tenant_id
}

output "principal_id" {
  description = "Object ID of the identity, used for data-plane role assignments."
  value       = azurerm_user_assigned_identity.this.principal_id
}

output "subject" {
  description = "OIDC subject claim this identity trusts."
  value       = local.subject
}
