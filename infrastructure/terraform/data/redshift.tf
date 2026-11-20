# Silver and gold: Redshift Serverless (ADR 005). Redshift generates the
# admin password and keeps it in Secrets Manager (rotated every 30 days by
# default); it never appears in Terraform state or in this repository.
resource "aws_redshiftserverless_namespace" "warehouse" {
  namespace_name        = "northstar"
  db_name               = "northstar"
  admin_username        = "nbi_admin"
  manage_admin_password = true
  iam_roles             = [aws_iam_role.redshift_copy.arn]
  default_iam_role_arn  = aws_iam_role.redshift_copy.arn
}

resource "aws_redshiftserverless_workgroup" "warehouse" {
  namespace_name = aws_redshiftserverless_namespace.warehouse.namespace_name
  workgroup_name = "northstar"

  # Base equals maximum: the warehouse never scales above the base, so its
  # cost per active hour is fixed at base_capacity_rpu RPU-hours.
  base_capacity = var.base_capacity_rpu
  max_capacity  = var.base_capacity_rpu

  # Reachable from Looker Studio and the operator only (security group), over TLS only.
  publicly_accessible  = true
  enhanced_vpc_routing = false
  port                 = 5439
  subnet_ids           = [for s in aws_subnet.warehouse : s.id]
  security_group_ids   = [aws_security_group.warehouse.id]

  config_parameter {
    parameter_key   = "require_ssl"
    parameter_value = "true"
  }

  depends_on = [aws_route_table_association.warehouse]
}

# The hard cap on compute: when the daily limit is reached, Redshift turns off
# user queries until the day ends (breach action `deactivate`).
resource "aws_redshiftserverless_usage_limit" "daily" {
  resource_arn  = aws_redshiftserverless_workgroup.warehouse.arn
  usage_type    = "serverless-compute"
  period        = "daily"
  amount        = var.daily_rpu_hours
  breach_action = "deactivate"
}

# AWS allows one `deactivate` limit per feature ("Only one DISABLE usage
# limit allowed per feature", CreateUsageLimit, 2026-11-20), so the monthly
# limit emits a CloudWatch metric instead (owner decision 2026-11-20).
resource "aws_redshiftserverless_usage_limit" "monthly" {
  resource_arn  = aws_redshiftserverless_workgroup.warehouse.arn
  usage_type    = "serverless-compute"
  period        = "monthly"
  amount        = var.monthly_rpu_hours
  breach_action = "emit-metric"
}
