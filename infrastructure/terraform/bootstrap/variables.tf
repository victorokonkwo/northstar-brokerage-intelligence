variable "region" {
  description = "AWS region for the baseline (owner decision 2026-11-19)."
  type        = string
  default     = "ca-central-1"
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget in USD (owner decision 2026-11-19)."
  type        = number
  default     = 10

  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "monthly_budget_usd must be positive."
  }
}

variable "budget_alert_email" {
  description = "Address for budget alerts. Personal data: pass it as TF_VAR_budget_alert_email, never commit it."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.budget_alert_email))
    error_message = "budget_alert_email must be an email address."
  }
}
