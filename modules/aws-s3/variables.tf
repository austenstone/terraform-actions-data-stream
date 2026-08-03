variable "github_owner_id" {
  description = "Numeric GitHub organization or enterprise ID."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_owner_id))
    error_message = "github_owner_id must be the numeric ID, not the slug."
  }
}

variable "github_owner_type" {
  description = "organization or enterprise."
  type        = string
  default     = "organization"

  validation {
    condition     = contains(["organization", "enterprise"], var.github_owner_type)
    error_message = "github_owner_type must be organization or enterprise."
  }
}

variable "bucket_name" {
  description = "S3 bucket that events are written to. Must be globally unique."
  type        = string
}

variable "role_name" {
  description = "Name of the IAM role the data stream assumes."
  type        = string
  default     = "github-actions-data-stream"
}

variable "create_oidc_provider" {
  description = "Create the GitHub OIDC provider. Set to false if the account already has one, which is common since regular Actions OIDC uses the same provider."
  type        = bool
  default     = true
}

variable "prefix" {
  description = "Optional key prefix. Scopes the IAM policy to matching keys only."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Tags applied to created resources."
  type        = map(string)
  default     = {}
}
