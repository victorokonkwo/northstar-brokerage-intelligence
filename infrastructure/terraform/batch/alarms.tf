# A failed step already emails its details (state machine). This alarm also
# catches what can't: a timed-out execution, or a failure of the alert itself.
resource "aws_cloudwatch_metric_alarm" "daily_close_failed" {
  alarm_name          = "northstar-daily-close-failed"
  alarm_description   = "A northstar-daily-close execution failed or timed out."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [data.aws_sns_topic.alerts.arn]

  metric_query {
    id          = "failed_or_timed_out"
    expression  = "failed + timed_out"
    label       = "Failed or timed-out executions"
    return_data = true
  }

  dynamic "metric_query" {
    for_each = { failed = "ExecutionsFailed", timed_out = "ExecutionsTimedOut" }
    content {
      id = metric_query.key
      metric {
        namespace   = "AWS/States"
        metric_name = metric_query.value
        dimensions  = { StateMachineArn = aws_sfn_state_machine.daily_close.arn }
        stat        = "Sum"
        period      = 300
      }
    }
  }
}
