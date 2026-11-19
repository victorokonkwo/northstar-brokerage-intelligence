provider "aws" {
  region = var.region

  default_tags {
    tags = {
      project     = "northstar-brokerage-intelligence"
      environment = "shared"
      managed_by  = "terraform"
      stack       = "bootstrap"
    }
  }
}
