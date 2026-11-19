# State lives in the bucket this stack created (migrated 2026-11-19). The
# bucket name contains the AWS account ID, so it is passed at init time
# instead of committed: `make tf-init-bootstrap` uses
# bucket=northstar-tfstate-<account id>-ca-central-1 (see state_bucket.tf).
terraform {
  backend "s3" {
    key          = "bootstrap/terraform.tfstate"
    region       = "ca-central-1"
    encrypt      = true
    use_lockfile = true
  }
}
