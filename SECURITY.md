# Security

## Scope

This project processes **synthetic data only**. Real customer, account, or transaction data must never enter the repository, local databases, cloud environments, logs, fixtures, or screenshots.

## Secrets

- Never commit credentials, API tokens, private keys, `.env` files, `profiles.yml` files that contain credentials, or Terraform state.
- Locally, secrets come from a git-ignored `.env` file. `.env.example` lists the variable names, with `<generate>` placeholders instead of secret values. `make env` creates `.env` from it with freshly generated random secrets and owner-only permissions (0600), and never overwrites an existing file.
- The local stack publishes its ports on `127.0.0.1` only, and its S3 store refuses anonymous access.
- In AWS, secrets are stored in AWS Secrets Manager and read at runtime by IAM roles with least-privilege policies.
- If a secret is committed by mistake, rotate it immediately. Removing it from Git history does not make it safe again.

## Cloud resources

- Every AWS resource is defined in Terraform. Nothing is created by hand in the console.
- Budget alerts must exist before any billable resource is created.
- S3 buckets block public access, enforce encryption at rest, and have lifecycle policies defined in Terraform.

## Dependencies

- Dependencies are locked (`uv.lock`). CI audits the lock file against the OSV vulnerability database (`uv audit`) on every pull request and push to `main`, and fails on any known vulnerability.
- Pull requests are scanned for committed secrets by the GitGuardian GitHub app. A finding is either fixed (and the secret rotated) or recorded in GitGuardian as a false positive with a reason.
- Adding a dependency needs a stated reason in the PR.

## Reporting a vulnerability

Open a private security advisory on the repository's GitHub page, or contact the repository owner directly. Do not open a public issue for a vulnerability.
