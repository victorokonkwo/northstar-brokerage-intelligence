# Tasks run in the warehouse VPC's public subnets with a public IP, so they reach
# AWS APIs, ECR, and S3 without a NAT gateway (ADR 010). Nothing may connect in.
resource "aws_security_group" "task" {
  name        = "northstar-batch-task"
  description = "Batch tasks: no inbound; HTTPS out, and Redshift in this VPC"
  vpc_id      = data.aws_vpc.warehouse.id

  tags = { Name = "northstar-batch-task" }
}

resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.task.id
  description       = "AWS APIs, ECR, and S3 over HTTPS"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "redshift" {
  security_group_id            = aws_security_group.task.id
  description                  = "Redshift Serverless, through its endpoint in this VPC"
  ip_protocol                  = "tcp"
  from_port                    = 5439
  to_port                      = 5439
  referenced_security_group_id = data.aws_security_group.warehouse.id
}

# The workgroup's side: the tasks may reach port 5439. They connect to the
# endpoint's private address (see ecs.tf), so the traffic stays in the VPC and
# this rule, which names the task security group, matches it.
resource "aws_vpc_security_group_ingress_rule" "warehouse_from_tasks" {
  security_group_id            = data.aws_security_group.warehouse.id
  description                  = "Batch tasks"
  ip_protocol                  = "tcp"
  from_port                    = 5439
  to_port                      = 5439
  referenced_security_group_id = aws_security_group.task.id
}
