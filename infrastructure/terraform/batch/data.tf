# What the data stack created, looked up by name, so this stack never edits it.
data "aws_caller_identity" "current" {}

data "aws_vpc" "warehouse" {
  tags = { Name = "northstar-warehouse" }
}

data "aws_subnets" "warehouse" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.warehouse.id]
  }
}

data "aws_security_group" "warehouse" {
  vpc_id = data.aws_vpc.warehouse.id
  name   = "northstar-warehouse"
}

data "aws_redshiftserverless_workgroup" "warehouse" {
  workgroup_name = "northstar"
}

data "aws_s3_bucket" "bronze" {
  bucket = "northstar-bronze-${data.aws_caller_identity.current.account_id}-${var.region}"
}

data "aws_sns_topic" "alerts" {
  name = "northstar-alerts"
}
