provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project     = "northstar-brokerage-intelligence"
      environment = "dev"
      managed_by  = "terraform"
      stack       = "batch"
    }
  }
}
