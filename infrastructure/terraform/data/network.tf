# A dedicated VPC for the workgroup: three public subnets in three AZs (the
# provider requires three; AWS requires at least two without enhanced VPC
# routing), an internet gateway, and no NAT gateway (no hourly charge).
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(sort(data.aws_availability_zones.available.names), 0, 3)
}

resource "aws_vpc" "warehouse" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "northstar-warehouse" }
}

resource "aws_internet_gateway" "warehouse" {
  vpc_id = aws_vpc.warehouse.id

  tags = { Name = "northstar-warehouse" }
}

resource "aws_subnet" "warehouse" {
  for_each = { for i, az in local.azs : az => i }

  vpc_id            = aws_vpc.warehouse.id
  availability_zone = each.key
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, each.value)

  tags = { Name = "northstar-warehouse-${each.key}" }
}

resource "aws_route_table" "warehouse" {
  vpc_id = aws_vpc.warehouse.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.warehouse.id
  }

  tags = { Name = "northstar-warehouse" }
}

resource "aws_route_table_association" "warehouse" {
  for_each = aws_subnet.warehouse

  subnet_id      = each.value.id
  route_table_id = aws_route_table.warehouse.id
}

# Inbound: the Redshift port from Looker Studio and the operator only. No
# egress rules: without enhanced VPC routing, COPY from S3 does not use this
# VPC, and nothing in it initiates connections.
resource "aws_security_group" "warehouse" {
  name        = "northstar-warehouse"
  description = "Redshift Serverless: inbound 5439 from Looker Studio and the operator only"
  vpc_id      = aws_vpc.warehouse.id

  tags = { Name = "northstar-warehouse" }
}

resource "aws_vpc_security_group_ingress_rule" "looker_studio" {
  for_each = toset(var.looker_studio_cidrs)

  security_group_id = aws_security_group.warehouse.id
  description       = "Looker Studio connectors"
  ip_protocol       = "tcp"
  from_port         = 5439
  to_port           = 5439
  cidr_ipv4         = each.value
}

resource "aws_vpc_security_group_ingress_rule" "operator" {
  count = length(var.operator_cidrs)

  security_group_id = aws_security_group.warehouse.id
  description       = "Operator"
  ip_protocol       = "tcp"
  from_port         = 5439
  to_port           = 5439
  cidr_ipv4         = var.operator_cidrs[count.index]
}
