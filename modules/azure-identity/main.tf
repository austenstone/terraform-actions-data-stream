locals {
  issuer   = "https://token.actions.githubusercontent.com"
  audience = "api://AzureADTokenExchange"
  subject  = "actions-data-stream:${var.github_owner_type}/${var.github_owner_id}"
}

# A user-assigned managed identity is used instead of an Entra app registration.
# Both support federated credentials, but a UAMI is an ARM resource, so it needs
# only Contributor on the subscription. App registrations need Entra directory
# rights, which most enterprises restrict.
resource "azurerm_user_assigned_identity" "this" {
  name                = var.name
  resource_group_name = var.resource_group_name
  location            = var.location
  tags                = var.tags
}

resource "azurerm_federated_identity_credential" "this" {
  name                      = "github-actions-data-stream"
  user_assigned_identity_id = azurerm_user_assigned_identity.this.id
  audience                  = [local.audience]
  issuer                    = local.issuer
  subject                   = local.subject
}
