variable "region" {
  description = "AWS region (the data stack's)."
  type        = string
  default     = "ca-central-1"
}

variable "image_tag" {
  description = "Tag of the batch image in ECR: the Git commit it was built from (make batch-image)."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{7,40}$", var.image_tag))
    error_message = "image_tag must be a Git commit SHA (no -dirty builds in the cloud)."
  }
}

variable "task_cpu" {
  description = "Fargate task CPU units (1024 = 1 vCPU)."
  type        = number
  default     = 1024
}

variable "task_memory" {
  description = "Fargate task memory in MiB."
  type        = number
  default     = 2048
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the tasks and the state machine."
  type        = number
  default     = 30
}

variable "schedule_enabled" {
  description = "Whether the daily close runs on its schedule. Off by default (owner decision 2026-11-20): the dev dataset covers Q1 2026 only, so runs are started on demand with a business date."
  type        = bool
  default     = false
}

variable "schemas" {
  description = "Warehouse schemas the cloud run reads and writes (the dev dataset's, as in the Airflow DAG)."
  type = object({
    raw                = string
    ledger             = string
    recon              = string
    dbt                = string
    published          = string
    published_controls = string
  })
  default = {
    raw                = "raw_dev"
    ledger             = "ledger_dev"
    recon              = "recon_dev"
    dbt                = "dbt_dataset"
    published          = "published_dev"
    published_controls = "published_controls_dev"
  }
}
