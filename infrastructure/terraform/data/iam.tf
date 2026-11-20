# The role Redshift assumes for COPY from bronze: read-only on that bucket.
# AWS asks for both service principals in the trust policy (docs/sources.md).
data "aws_iam_policy_document" "redshift_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["redshift-serverless.amazonaws.com", "redshift.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "redshift_copy" {
  name               = "northstar-redshift-copy"
  description        = "Redshift Serverless COPY from the bronze bucket (read-only)"
  assume_role_policy = data.aws_iam_policy_document.redshift_trust.json
}

data "aws_iam_policy_document" "bronze_read" {
  statement {
    sid       = "ListBronze"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.bronze.arn]
  }

  statement {
    sid       = "ReadBronze"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.bronze.arn}/*"]
  }
}

resource "aws_iam_role_policy" "bronze_read" {
  name   = "bronze-read"
  role   = aws_iam_role.redshift_copy.id
  policy = data.aws_iam_policy_document.bronze_read.json
}
