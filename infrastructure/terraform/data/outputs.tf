output "bronze_bucket_name" {
  description = "S3 bucket for the bronze (raw) layer."
  value       = aws_s3_bucket.bronze.bucket
}

output "redshift_endpoint" {
  description = "Workgroup endpoint host and port."
  value = {
    host = aws_redshiftserverless_workgroup.warehouse.endpoint[0].address
    port = aws_redshiftserverless_workgroup.warehouse.endpoint[0].port
  }
}

output "redshift_admin_secret_arn" {
  description = "Secrets Manager secret holding the admin credentials (managed by Redshift)."
  value       = aws_redshiftserverless_namespace.warehouse.admin_password_secret_arn
}

output "redshift_copy_role_arn" {
  description = "IAM role for COPY from bronze (the namespace's default role)."
  value       = aws_iam_role.redshift_copy.arn
}

output "usage_limits_rpu_hours" {
  description = "Compute limits: daily turns off user queries, monthly emits a metric."
  value       = { daily = var.daily_rpu_hours, monthly = var.monthly_rpu_hours }
}

output "alerts_topic_arn" {
  description = "SNS topic that emails the operator (cloud run failures, monthly compute)."
  value       = aws_sns_topic.alerts.arn
}
