variable "resource_group_name" {
  description = "Existing resource group to deploy into."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
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
  description = "Storage account name. Must be 3-24 lowercase alphanumeric characters and globally unique."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "storage_account_name must be 3-24 lowercase alphanumeric characters."
  }
}

variable "container_name" {
  description = "Blob container that events are written to."
  type        = string
  default     = "actions-events"
}

variable "identity_name" {
  description = "Name of the user-assigned managed identity."
  type        = string
  default     = "actions-data-stream"
}

variable "tags" {
  description = "Tags applied to created resources."
  type        = map(string)
  default     = {}
}
