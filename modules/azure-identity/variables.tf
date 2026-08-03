variable "name" {
  description = "Name of the user-assigned managed identity."
  type        = string
}

variable "resource_group_name" {
  description = "Resource group to create the identity in."
  type        = string
}

variable "location" {
  description = "Azure region."
  type        = string
}

variable "github_owner_id" {
  description = "Numeric GitHub organization or enterprise ID. Shown in the data stream UI as the subject claim suffix."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_owner_id))
    error_message = "github_owner_id must be numeric. The data stream subject claim uses the numeric ID, not the slug."
  }
}

variable "github_owner_type" {
  description = "Whether the data stream is configured at the organization or enterprise level."
  type        = string
  default     = "organization"

  validation {
    condition     = contains(["organization", "enterprise"], var.github_owner_type)
    error_message = "github_owner_type must be either organization or enterprise."
  }
}

variable "tags" {
  description = "Tags applied to created resources."
  type        = map(string)
  default     = {}
}
