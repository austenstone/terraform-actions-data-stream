module "identity" {
  source = "../azure-identity"

  name                = var.identity_name
  resource_group_name = var.resource_group_name
  location            = var.location
  github_owner_id     = var.github_owner_id
  github_owner_type   = var.github_owner_type
  tags                = var.tags
}

resource "azurerm_storage_account" "this" {
  name                     = var.storage_account_name
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"
  tags                     = var.tags

  # The data stream authenticates with OIDC, so no account keys or SAS tokens
  # are needed. Disabling shared keys forces Entra auth for every caller.
  shared_access_key_enabled       = false
  allow_nested_items_to_be_public = false
}

resource "azurerm_storage_container" "this" {
  name                  = var.container_name
  storage_account_id    = azurerm_storage_account.this.id
  container_access_type = "private"
}

# Data-plane role. Contributor on the subscription is NOT sufficient to write
# blobs; the identity needs an explicit data role. Creating the assignment
# itself requires User Access Administrator or Owner, which is often a
# different person than the one running Terraform.
resource "azurerm_role_assignment" "blob_writer" {
  count = var.create_role_assignment ? 1 : 0

  scope                = azurerm_storage_container.this.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = module.identity.principal_id
  principal_type       = "ServicePrincipal"
}
