variable "subscription_id" {
  description = "Azure subscription ID."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to create."
  type        = string
  default     = "actions-data-stream"
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

variable "storage_account_name" {
  description = "Globally unique storage account name, 3-24 lowercase alphanumeric."
  type        = string
}

variable "tags" {
  description = "Tags applied to created resources."
  type        = map(string)
  default     = {}
}
