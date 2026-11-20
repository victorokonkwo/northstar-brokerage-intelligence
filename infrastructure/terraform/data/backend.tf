# State lives in the bootstrap stack's bucket. Its name contains the AWS
# account ID, so `make tf-init-data` passes it at init time instead of
# committing it (as for the bootstrap stack).
terraform {
  backend "s3" {
    key          = "data/terraform.tfstate"
    region       = "ca-central-1"
    encrypt      = true
    use_lockfile = true
  }
}
