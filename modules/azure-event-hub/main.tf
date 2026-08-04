module "identity" {
  source = "../azure-identity"

  name                = var.identity_name
  resource_group_name = var.resource_group_name
  location            = var.location
  github_owner_id     = var.github_owner_id
  github_owner_type   = var.github_owner_type
  tags                = var.tags
}

resource "azurerm_eventhub_namespace" "this" {
  name                = var.namespace_name
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = var.sku
  capacity            = var.capacity
  tags                = var.tags

  # The data stream authenticates with OIDC. Disabling local auth removes the
  # SAS connection strings entirely so Entra is the only way in.
  local_authentication_enabled = false
  minimum_tls_version          = "1.2"
}

resource "azurerm_eventhub" "this" {
  name              = var.hub_name
  namespace_id      = azurerm_eventhub_namespace.this.id
  partition_count   = var.partition_count
  message_retention = var.retention_days
}

# Data-plane role. Contributor on the subscription is NOT sufficient to send
# events; the identity needs an explicit data role. Creating the assignment
# itself requires User Access Administrator or Owner.
#
# This is why azure-kusto is the only Azure sink a plain Contributor can stand
# up end to end: Kusto grants data-plane access through a Kusto principal
# assignment rather than Azure RBAC. Blob and Event Hubs both go through
# Microsoft.Authorization/roleAssignments/write and will fail without it.
resource "azurerm_role_assignment" "sender" {
  count = var.create_role_assignment ? 1 : 0

  scope                = azurerm_eventhub.this.id
  role_definition_name = "Azure Event Hubs Data Sender"
  principal_id         = module.identity.principal_id
  principal_type       = "ServicePrincipal"
}
