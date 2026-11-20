# Alerts (roadmap 4G). One SNS topic that emails the operator: the batch stack
# sends failed cloud runs here, and this stack sends the monthly compute limit.
# AWS asks the address to confirm the subscription by email before it delivers.
resource "aws_sns_topic" "alerts" {
  name = "northstar-alerts"
}

resource "aws_sns_topic_subscription" "alert_email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# The monthly usage limit only emits a metric (AWS allows one `deactivate`
# limit per feature; owner decision 2026-11-20). This alarm turns that metric
# into an email. Redshift reports UsageLimitConsumed hourly, in whole RPU-hours,
# per limit, with UsageType SERVERLESS_COMPUTE (CloudWatch, 2026-11-20).
resource "aws_cloudwatch_metric_alarm" "monthly_compute" {
  alarm_name        = "northstar-redshift-monthly-compute"
  alarm_description = "Redshift Serverless used its monthly RPU-hours (${var.monthly_rpu_hours}); queries keep running, the daily cap still applies."
  namespace         = "AWS/Redshift-Serverless"
  metric_name       = "UsageLimitConsumed"
  dimensions = {
    UsageLimitId = aws_redshiftserverless_usage_limit.monthly.id
    Workgroup    = aws_redshiftserverless_workgroup.warehouse.workgroup_name
    UsageType    = "SERVERLESS_COMPUTE"
  }
  statistic           = "Maximum"
  period              = 3600
  evaluation_periods  = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = var.monthly_rpu_hours
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
