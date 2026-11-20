resource "aws_ecs_cluster" "batch" {
  name = "northstar-batch"
}

resource "aws_cloudwatch_log_group" "tasks" {
  name              = "/northstar/batch"
  retention_in_days = var.log_retention_days
}

locals {
  # The endpoint's private address in this VPC: connecting there keeps the
  # traffic inside the VPC, where the security-group rules apply (network.tf).
  # TLS is still required (sslmode=require).
  redshift_private_ip = data.aws_redshiftserverless_workgroup.warehouse.endpoint[0].vpc_endpoint[0].network_interface[0].private_ip_address

  environment = {
    NBI_WAREHOUSE                 = "redshift"
    NBI_DBT_TARGET                = "redshift"
    NBI_RS_HOST                   = local.redshift_private_ip
    NBI_RS_PORT                   = tostring(data.aws_redshiftserverless_workgroup.warehouse.endpoint[0].port)
    NBI_RS_DATABASE               = "northstar"
    NBI_RS_WORKGROUP              = data.aws_redshiftserverless_workgroup.warehouse.workgroup_name
    NBI_AWS_REGION                = var.region
    NBI_BRONZE_BUCKET             = data.aws_s3_bucket.bronze.bucket
    NBI_RAW_SCHEMA                = var.schemas.raw
    NBI_LEDGER_SCHEMA             = var.schemas.ledger
    NBI_RECON_SCHEMA              = var.schemas.recon
    NBI_DBT_SCHEMA                = var.schemas.dbt
    NBI_PUBLISHED_SCHEMA          = var.schemas.published
    NBI_PUBLISHED_CONTROLS_SCHEMA = var.schemas.published_controls
  }
}

resource "aws_ecs_task_definition" "batch" {
  family                   = "northstar-batch"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "ARM64"
  }

  container_definitions = jsonencode([{
    name      = "northstar"
    image     = "${aws_ecr_repository.batch.repository_url}:${var.image_tag}"
    essential = true
    environment = [
      for name in sort(keys(local.environment)) : { name = name, value = local.environment[name] }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.tasks.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "step"
      }
    }
  }])
}
