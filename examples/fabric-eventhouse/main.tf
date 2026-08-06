terraform {
  required_version = ">= 1.5"
  required_providers {
    fabric = {
      source  = "microsoft/fabric"
      version = "~> 1.12"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.0"
    }
  }
}

provider "azurerm" {
  features {}
}

# Uses the same az CLI login as the azurerm provider and as scripts/apply-kql.sh,
# so a single `az login` covers the whole apply.
provider "fabric" {
  use_cli = true
}

resource "azurerm_resource_group" "this" {
  name     = var.resource_group_name
  location = var.location
}

module "eventhouse" {
  source = "../../modules/fabric-eventhouse"

  workspace_id        = var.workspace_id
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  github_owner_id     = var.github_owner_id
  github_owner_type   = var.github_owner_type
}

output "sink_config" {
  description = "Paste into the data stream API, or pipe it: terraform output -json sink_config"
  value       = module.eventhouse.sink_config
}

output "subject" {
  value = module.eventhouse.subject
}

output "cluster_uri" {
  value = module.eventhouse.cluster_uri
}
