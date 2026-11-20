# State lives in the bootstrap stack's bucket, next to the data stack's. The
# bucket name contains the AWS account ID, so `make tf-init-batch` passes it at
# init time instead of committing it.
terraform {
  backend "s3" {
    key          = "batch/terraform.tfstate"
    region       = "ca-central-1"
    encrypt      = true
    use_lockfile = true
  }
}
