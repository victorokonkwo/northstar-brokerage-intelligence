terraform {
  # use_lockfile (native S3 state locking, used after state migration) needs 1.10+.
  required_version = ">= 1.10, < 2.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.73"
    }
  }
}
