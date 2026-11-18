# Security

## Scope

This project processes **synthetic data only**. Real customer, account, or transaction data must never enter the repository, local databases, cloud environments, logs, fixtures, or screenshots.

## Secrets

- Never commit credentials, API tokens, private keys, `.env` files, `profiles.yml` files that contain credentials, or Terraform state.
- Locally, secrets come from environment variables. A `.env.example` that lists variable *names* only will be added in Phase 1.
- In AWS, secrets are stored in AWS Secrets Manager and read at runtime by IAM roles with least-privilege policies.
- If a secret is committed by mistake, rotate it immediately. Removing it from Git history does not make it safe again.

## Cloud resources

- Every AWS resource is defined in Terraform. Nothing is created by hand in the console.
- Budget alerts must exist before any billable resource is created.
- S3 buckets block public access, enforce encryption at rest, and have lifecycle policies defined in Terraform.

## Dependencies

- Dependencies are locked (`uv.lock`) and scanned in CI once CI exists (Phase 1).
- Adding a dependency needs a stated reason in the PR.

## Reporting a vulnerability

Open a private security advisory on the repository's GitHub page, or contact the repository owner directly. Do not open a public issue for a vulnerability.
