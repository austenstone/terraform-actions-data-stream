terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
}

module "sink" {
  source = "../../modules/aws-s3"

  github_owner_id      = var.github_owner_id
  github_owner_type    = var.github_owner_type
  bucket_name          = var.bucket_name
  create_oidc_provider = var.create_oidc_provider
  tags                 = var.tags
}
