# Terraform bootstrap stack

| | |
|---|---|
| Status | Written and validated; **not applied** until the owner approves the plan |
| Region | `ca-central-1` (owner decision 2026-11-19) |

This stack creates the account-level baseline that every later AWS stack depends on:

| Resource | Why |
|---|---|
| `aws_budgets_budget.monthly` | USD 10 per month cost budget. It emails at 80% of actual spend and 100% of forecast spend. Notification-only budgets have no charge ([AWS Budgets pricing](https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/)). |
| `aws_s3_bucket.state` and its settings | Remote Terraform state, `northstar-tfstate-<account id>-<region>`. It is private (public access blocked, bucket-owner-enforced), versioned (old versions expire after 90 days), encrypted (SSE-S3), TLS-only, and `prevent_destroy`. |

CloudWatch OTLP export is deferred to Phase 3, when the first workload runs in AWS (owner decision 2026-11-19).

## The alert email

The address is personal data and is never committed. Put it in the git-ignored root `.env`:

```
TF_VAR_budget_alert_email=you@example.com
```

The Make targets load `.env`, and Terraform reads `TF_VAR_*` variables itself. The variable is `sensitive`, so it is not printed in plans. The address is still stored in the state file, which is local and git-ignored until migration, then in the encrypted state bucket.

## Procedure

Run from the repository root, with AWS credentials for the target account (`aws login`).

1. **Validate (no AWS access).** `make tf-validate`
2. **Plan.** `make tf-plan-bootstrap` writes `bootstrap.tfplan` (git-ignored). Review it with the owner.
3. **Budget first** (CONTRIBUTING.md W6: budget alerts exist before any billable resource):
   ```bash
   set -a && . ./.env && set +a
   terraform -chdir=infrastructure/terraform/bootstrap apply -target=aws_budgets_budget.monthly
   ```
4. **The rest**, after checking that the budget exists:
   ```bash
   terraform -chdir=infrastructure/terraform/bootstrap apply
   ```
5. **Move this stack's state into the bucket.** Add a `backend "s3"` block (bucket from the `state_bucket_name` output, `key = "bootstrap/terraform.tfstate"`, `region = "ca-central-1"`, `use_lockfile = true`, `encrypt = true`), run `terraform init -migrate-state`, and commit the block. Until then the state is the local, git-ignored `terraform.tfstate` in this folder. Don't delete it.

Nothing here is applied by CI. CI only runs `make tf-validate`.
