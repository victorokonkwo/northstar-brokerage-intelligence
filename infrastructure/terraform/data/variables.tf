variable "region" {
  description = "AWS region (owner decision 2026-11-19)."
  type        = string
  default     = "ca-central-1"
}

variable "vpc_cidr" {
  description = "CIDR of the warehouse VPC. It holds only the workgroup's network interfaces."
  type        = string
  default     = "10.42.0.0/16"
}

variable "base_capacity_rpu" {
  description = "Workgroup base and maximum capacity in RPUs. 4 is the minimum; available in ca-central-1 since 2026-05-29 (docs/sources.md)."
  type        = number
  default     = 4

  validation {
    condition     = var.base_capacity_rpu == 4 || (var.base_capacity_rpu >= 8 && var.base_capacity_rpu % 8 == 0)
    error_message = "base_capacity_rpu must be 4, or a multiple of 8 from 8 (AWS: serverless-capacity)."
  }
}

variable "daily_rpu_hours" {
  description = "Daily compute limit in RPU-hours. On breach, Redshift turns off user queries until the next period."
  type        = number
  default     = 8 # owner decision 2026-11-20 (was 4; raised for the 4F-c parity rerun and kept)

  validation {
    condition     = var.daily_rpu_hours > 0
    error_message = "daily_rpu_hours must be positive."
  }
}

variable "monthly_rpu_hours" {
  description = "Monthly compute limit in RPU-hours. On breach, Redshift emits a CloudWatch metric (AWS allows only one deactivating limit)."
  type        = number
  default     = 60

  validation {
    condition     = var.monthly_rpu_hours > 0
    error_message = "monthly_rpu_hours must be positive."
  }
}

variable "looker_studio_cidrs" {
  description = "Looker Studio's published IPv4 range for database connectors (docs/sources.md)."
  type        = list(string)
  default     = ["142.251.74.0/23"]
}

variable "operator_cidrs" {
  description = "The operator's own public address(es), e.g. [\"203.0.113.7/32\"]. Personal data: pass it as TF_VAR_operator_cidrs from .env, never commit it."
  type        = list(string)
  sensitive   = true

  validation {
    condition = alltrue([
      for c in var.operator_cidrs : can(cidrhost(c, 0)) && can(regex("^[0-9.]+/(2[4-9]|3[0-2])$", c))
    ])
    error_message = "operator_cidrs must be IPv4 CIDRs no wider than /24."
  }
}

variable "alert_email" {
  description = "Address for the alerts topic. Personal data: the Make targets pass TF_VAR_budget_alert_email; never commit it."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alert_email))
    error_message = "alert_email must be an email address."
  }
}

variable "stage_expiry_days" {
  description = "Days before load-staging objects under _stage/ expire."
  type        = number
  default     = 7

  validation {
    condition     = var.stage_expiry_days >= 1
    error_message = "stage_expiry_days must be at least 1."
  }
}
