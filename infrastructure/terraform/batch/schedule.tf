# 06:00 America/Toronto, closing the previous day, as the Airflow DAG does.
# Disabled by default (variables.tf): the dev dataset covers Q1 2026 only.
resource "aws_scheduler_schedule" "daily_close" {
  name                         = "northstar-daily-close"
  state                        = var.schedule_enabled ? "ENABLED" : "DISABLED"
  schedule_expression          = "cron(0 6 * * ? *)"
  schedule_expression_timezone = "America/Toronto"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_sfn_state_machine.daily_close.arn
    role_arn = aws_iam_role.scheduler.arn
    # The state machine closes the day before this time (UTC, ISO 8601).
    input = jsonencode({ scheduled_time = "<aws.scheduler.scheduled-time>" })
  }
}
