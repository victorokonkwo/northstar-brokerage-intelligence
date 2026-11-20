# Bronze: the raw layer in S3 (ADR 005, system overview §6). Objects are
# written put-if-absent by ingestion and never rewritten, so versioning keeps
# any accidental overwrite or delete recoverable; nothing expires old versions.
data "aws_caller_identity" "current" {}

locals {
  bronze_bucket_name = "northstar-bronze-${data.aws_caller_identity.current.account_id}-${var.region}"
}

resource "aws_s3_bucket" "bronze" {
  bucket = local.bronze_bucket_name
}

resource "aws_s3_bucket_ownership_controls" "bronze" {
  bucket = aws_s3_bucket.bronze.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "bronze" {
  bucket                  = aws_s3_bucket.bronze.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "bronze" {
  bucket = aws_s3_bucket.bronze.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "bronze" {
  bucket = aws_s3_bucket.bronze.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "bronze" {
  bucket = aws_s3_bucket.bronze.id

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  # Load staging (redshift.Stage): JSON Lines copied into Redshift, then never
  # read again. Content-addressed, so a later replay stages the file anew.
  rule {
    id     = "expire-load-staging"
    status = "Enabled"

    filter {
      prefix = "_stage/"
    }

    expiration {
      days = var.stage_expiry_days
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }
  }

  depends_on = [aws_s3_bucket_versioning.bronze]
}

data "aws_iam_policy_document" "bronze_tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.bronze.arn, "${aws_s3_bucket.bronze.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "bronze" {
  bucket = aws_s3_bucket.bronze.id
  policy = data.aws_iam_policy_document.bronze_tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.bronze]
}
