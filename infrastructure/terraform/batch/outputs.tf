output "ecr_repository_url" {
  description = "Where make batch-image pushes the image."
  value       = aws_ecr_repository.batch.repository_url
}

output "state_machine_arn" {
  description = "Start a run: aws stepfunctions start-execution --state-machine-arn <this> --input '{\"business_date\": \"YYYY-MM-DD\"}'."
  value       = aws_sfn_state_machine.daily_close.arn
}

output "task_role_name" {
  description = "The tasks' IAM role. Its database user needs the pipeline role (redshift-bootstrap --grant-to)."
  value       = aws_iam_role.task.name
}

output "task_log_group" {
  description = "CloudWatch Logs group of the tasks."
  value       = aws_cloudwatch_log_group.tasks.name
}
