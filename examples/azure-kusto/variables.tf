variable "subscription_id" {
  description = "Azure subscription ID."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to create."
  type        = string
  default     = "actions-data-stream-kusto"
}

variable "location" {
  description = "Azure region."
  type        = string
  default     = "eastus"
}

variable "github_owner_id" {
  description = "Numeric GitHub organization or enterprise ID."
  type        = string
}

variable "github_owner_type" {
  description = "organization or enterprise."
  type        = string
  default     = "organization"
}

variable "cluster_name" {
  description = "Globally unique Kusto cluster name."
  type        = string
}

variable "ingestion_type" {
  description = "streaming or queued."
  type        = string
  default     = "streaming"
}

variable "sku_name" {
  description = "Cluster SKU. Dev SKUs run on spare capacity and can fail with InsufficientResourcesForSubscription; try another region or SKU if so."
  type        = string
  default     = "Dev(No SLA)_Standard_E2a_v4"
}

variable "tags" {
  description = "Tags applied to created resources."
  type        = map(string)
  default     = {}
}

variable "enrichment_subjects" {
  description = "GitHub OIDC subjects allowed to run the enrichment scripts against this database. Empty disables the identity entirely. See examples/enrichment-workflow.yml."
  type        = list(string)
  default     = []
}
