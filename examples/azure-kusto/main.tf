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
  source = "../../modules/azure-kusto"

  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  github_owner_id     = var.github_owner_id
  github_owner_type   = var.github_owner_type
  cluster_name        = var.cluster_name
  sku_name            = var.sku_name
  ingestion_type      = var.ingestion_type
  enrichment_subjects = var.enrichment_subjects
  tags                = var.tags
}
