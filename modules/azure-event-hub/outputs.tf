output "sink_config" {
  description = "Config block for the azure_event_hub sink. Feed straight to the data stream API."
  value = {
    auth_type = "oidc"
    tenant_id = module.identity.tenant_id
    client_id = module.identity.client_id
    namespace = azurerm_eventhub_namespace.this.name
    hub       = azurerm_eventhub.this.name
  }
}

output "subject" {
  description = "OIDC subject claim this identity trusts. Must match the data stream UI exactly."
  value       = module.identity.subject
}

output "role_assignment_command" {
  description = "Hand this to whoever holds User Access Administrator when create_role_assignment is false."
  value = format(
    "az role assignment create --assignee-object-id %s --assignee-principal-type ServicePrincipal --role \"Azure Event Hubs Data Sender\" --scope %s",
    module.identity.principal_id,
    azurerm_eventhub.this.id
  )
}
