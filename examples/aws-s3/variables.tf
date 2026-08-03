variable "region" {
  description = "AWS region."
  type        = string
  default     = "us-east-1"
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

variable "bucket_name" {
  description = "Globally unique S3 bucket name."
  type        = string
}

variable "create_oidc_provider" {
  description = "Set to false if the account already has the GitHub OIDC provider."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags applied to created resources."
  type        = map(string)
  default     = {}
}
