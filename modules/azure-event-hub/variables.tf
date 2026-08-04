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

variable "namespace_name" {
  description = "Event Hubs namespace name. Must be globally unique, 6-50 characters, alphanumeric and hyphens."
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9-]{4,48}[a-zA-Z0-9]$", var.namespace_name))
    error_message = "namespace_name must be 6-50 characters, start with a letter, end alphanumeric, and contain only letters, digits and hyphens."
  }
}

variable "hub_name" {
  description = "Event hub that events are sent to."
  type        = string
  default     = "actions-events"
}

variable "sku" {
  description = "Namespace SKU. Basic caps retention at 1 day and allows a single consumer group, which is usually too tight for real consumers."
  type        = string
  default     = "Standard"

  validation {
    condition     = contains(["Basic", "Standard", "Premium"], var.sku)
    error_message = "sku must be Basic, Standard or Premium."
  }
}

variable "capacity" {
  description = "Throughput units. One TU handles 1 MB/s or 1000 events/s ingress, which is far more than a typical org emits."
  type        = number
  default     = 1
}

variable "partition_count" {
  description = "Partitions on the hub. The data stream does not set a partition key, so events land round-robin regardless of this value; more partitions only buys consumer parallelism."
  type        = number
  default     = 4
}

variable "retention_days" {
  description = "Message retention in days. Basic SKU only supports 1."
  type        = number
  default     = 1
}

variable "identity_name" {
  description = "Name of the user-assigned managed identity."
  type        = string
  default     = "actions-data-stream"
}

variable "create_role_assignment" {
  description = "Create the Azure Event Hubs Data Sender assignment. Requires User Access Administrator or Owner; plain Contributor is not enough. Set to false to have someone else apply it, then use the role_assignment_command output."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags applied to created resources."
  type        = map(string)
  default     = {}
}
