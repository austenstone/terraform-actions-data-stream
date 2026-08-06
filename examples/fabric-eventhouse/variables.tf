variable "workspace_id" {
  description = "Fabric workspace ID. It is the GUID in the workspace URL: app.fabric.microsoft.com/groups/<workspace_id>. The workspace must be on a Fabric capacity, not Pro."
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

variable "resource_group_name" {
  description = "Resource group for the managed identity."
  type        = string
  default     = "actions-data-stream"
}

variable "location" {
  description = "Azure region for the managed identity."
  type        = string
  default     = "eastus"
}
