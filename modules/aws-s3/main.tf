data "aws_caller_identity" "current" {}

locals {
  issuer_host = "token.actions.githubusercontent.com"
  audience    = "sts.amazonaws.com"
  subject     = "actions-data-stream:${var.github_owner_type}/${var.github_owner_id}"

  oidc_provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.this[0].arn : "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/${local.issuer_host}"

  key_pattern = var.prefix == "" ? "*" : "${trim(var.prefix, "/")}/*"
}

resource "aws_iam_openid_connect_provider" "this" {
  count = var.create_oidc_provider ? 1 : 0

  url             = "https://${local.issuer_host}"
  client_id_list  = [local.audience]
  thumbprint_list = []
  tags            = var.tags
}

# The sub condition is what actually scopes this role to your org. Without it,
# any GitHub tenant could assume the role.
data "aws_iam_policy_document" "assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.issuer_host}:sub"
      values   = [local.subject]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.issuer_host}:aud"
      values   = [local.audience]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = var.role_name
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
  tags               = var.tags
}

resource "aws_s3_bucket" "this" {
  bucket = var.bucket_name
  tags   = var.tags
}

resource "aws_s3_bucket_public_access_block" "this" {
  bucket                  = aws_s3_bucket.this.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "write" {
  statement {
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.this.arn}/${local.key_pattern}"]
  }
}

resource "aws_iam_role_policy" "write" {
  name   = "actions-data-stream-write"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.write.json
}
