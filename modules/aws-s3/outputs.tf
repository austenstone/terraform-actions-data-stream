output "sink_config" {
  description = "Config block for the s3 sink. Feed straight to the data stream API."
  value = merge(
    {
      auth_type = "oidc"
      role_arn  = aws_iam_role.this.arn
      region    = aws_s3_bucket.this.region
      bucket    = aws_s3_bucket.this.bucket
    },
    var.prefix == "" ? {} : { prefix = var.prefix }
  )
}

output "subject" {
  description = "OIDC subject claim this role trusts."
  value       = local.subject
}
