terraform {
  required_version = ">= 1.5"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.0"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
  tags     = var.tags
}

module "sink" {
  source = "../../modules/azure-event-hub"

  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  github_owner_id     = var.github_owner_id
  github_owner_type   = var.github_owner_type
  namespace_name      = var.namespace_name
  tags                = var.tags
}
