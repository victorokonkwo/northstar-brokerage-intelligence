# The daily close: one Fargate task per step of the Airflow DAG, in order
# (state_machine.json.tftpl, ADR 010).
resource "aws_sfn_state_machine" "daily_close" {
  name     = "northstar-daily-close"
  type     = "STANDARD"
  role_arn = aws_iam_role.states.arn
  definition = templatefile("${path.module}/state_machine.json.tftpl", {
    cluster_arn         = aws_ecs_cluster.batch.arn
    task_definition_arn = aws_ecs_task_definition.batch.arn
    subnets_json        = jsonencode(sort(data.aws_subnets.warehouse.ids))
    security_group_id   = aws_security_group.task.id
    alerts_topic_arn    = data.aws_sns_topic.alerts.arn
  })
}
