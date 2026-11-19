output "state_bucket_name" {
  description = "S3 bucket that holds Terraform state."
  value       = aws_s3_bucket.state.bucket
}

output "budget_name" {
  description = "Name of the monthly cost budget."
  value       = aws_budgets_budget.monthly.name
}
