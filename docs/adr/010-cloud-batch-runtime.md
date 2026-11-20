# 010. Cloud batch runtime: EventBridge Scheduler, Step Functions, and ECS Fargate

| | |
|---|---|
| Status | Accepted |
| Date | 2026-11-20 |
| Deciders | Project owner |

## Context

The pipeline runs on Redshift since Phase 4F: ingest, load, dbt, reconciliation, and publication ([cloud warehouse](../architecture/cloud-warehouse.md)). Locally, Airflow 3.4.0 orchestrates it, with the DAG `northstar_daily` ([ADR 003](003-batch-first-processing.md), batch first). The dev environment in the [system overview](../architecture/system-overview.md) §5 lists ECR, ECS Fargate, and CloudWatch for running it in AWS.

Facts (ca-central-1, AWS Price List API, 2026-11-20; [sources.md](../sources.md)):

| Service | Price |
|---|---|
| Fargate, ARM | USD 0.03565 per vCPU-hour, 0.00389 per GB-hour |
| Fargate, x86 | USD 0.04456 per vCPU-hour, 0.004865 per GB-hour |
| Step Functions, standard | USD 0.000025 per state transition |
| EventBridge Scheduler | first 14 million invocations free, then USD 1.10 per million |
| ECR | USD 0.10 per GB-month |
| CloudWatch Logs | USD 0.55 per GB ingested |
| CloudWatch alarm | USD 0.10 per alarm metric per month |
| Amazon MWAA, smallest environment | USD 0.319 per hour, about 233 per month |
| AWS Glue ETL | USD 0.44 per DPU-hour (0.308 on Glue 6.0 and later) |

## Options considered

1. **Amazon MWAA (managed Airflow).** It would run the same DAG unchanged. But it is billed by the hour whether or not anything runs, at about USD 233 a month at its smallest size, against a project budget of a few dollars.
2. **AWS Glue.** Its jobs transform data, and transformation belongs to dbt ([ADR 005](005-local-postgres-cloud-redshift.md)). Running the CLI and dbt in Glue Python jobs would fight the tool. It costs USD 0.44 per DPU-hour, or 0.308 on Glue 6.0 and later.
3. **EventBridge Scheduler, then Step Functions, then ECS Fargate tasks.** Pay per run, with nothing running between runs. One container image, built from the project's lock file, runs each DAG step as a task.

## Decision

Option 3 (owner decision 2026-11-20). It is checkable in `infrastructure/terraform/batch/`:

- **One image** (`infrastructure/docker/batch/Dockerfile`):
  - built from the lock file, for linux/arm64, and run as a non-root user
  - tagged with the Git commit it was built from, in an ECR repository whose tags are immutable
  - Graviton costs about 20% less than x86, and the image builds natively on the developer's arm64 machine
- **One Fargate task per DAG step** (`orchestration/cloud/run_step.sh`):
  - the steps are the DAG's, in the DAG's order
  - ingest and load run as one task, because the load needs the ingestion run's ID, and Step Functions can't read a container's output the way Airflow's XCom does
  - the script holds no business logic: each step calls the `northstar` CLI or dbt
- **Step Functions** (`northstar-daily-close`, standard, JSONata) runs the tasks in order:
  - every task is followed by a check that continues only on exit code 0
  - exit code 99 from the business-day check ends the run successfully, as the DAG skips it
  - any other exit code, and any failure of the ECS integration, sends an email with the business date and the step, then fails the execution
- **Business date:**
  - an execution started by hand takes `{"business_date": "YYYY-MM-DD"}`, the way a backfill does
  - a scheduled one closes the day before its scheduled time
- **The schedule** (EventBridge Scheduler, 06:00 America/Toronto, as in the DAG) is created **disabled**. Owner decision 2026-11-20: the dev dataset covers Q1 2026, so runs are started on demand. Enabling it is one variable.
- **Network:**
  - tasks run in the warehouse VPC's public subnets with public IPs, so there's no NAT gateway, which would add an hourly charge
  - their security group accepts no inbound traffic, and allows outbound only to HTTPS and to the workgroup on port 5439
  - they reach Redshift through the workgroup's private VPC address, so that traffic stays inside the VPC
- **Credentials:** no passwords. The task role gets temporary Redshift credentials (`GetCredentials`), and its database user is granted the pipeline role once by the admin (`redshift-bootstrap --grant-to`). Each role has only what it uses:
  - execution role: pull the image, write logs
  - task role: Redshift credentials, and the bronze prefixes `raw/`, `quarantine/`, `audit/`, and `_stage/`
  - state machine role: the ECS integration's documented permissions, passing the two roles to ECS, and publishing alerts
  - scheduler role: starting the state machine
- **Observability:**
  - task logs in CloudWatch, kept 30 days
  - an alarm on failed or timed-out executions
  - the data stack's alarm on the monthly Redshift usage limit
  - both alarms, and the per-step failure emails, go to one SNS topic in the data stack
- **Staging expiry:** load-staging objects under `_stage/` expire after 7 days (data stack).

## Consequences

- **Cost:** a run's Fargate cost is small. One vCPU and 2 GB for about 30 task-minutes is about USD 0.02. Redshift compute dominates, under the daily and monthly limits of 4E. The first recorded run measures both.
- **Two orchestrators.** Airflow runs locally and in CI, and Step Functions in AWS, so a DAG change needs the matching change in `run_step.sh` and the state machine. `tests/unit/test_cloud_batch.py` checks that they agree:
  - the steps are the DAG's, in its order
  - every `northstar` command in the script parses with the CLI
  - every variable the script reads is set
  - a step continues only after exit code 0
- **Retries differ from Airflow's.** Step Functions retries integration errors (two more attempts, with backoff). A non-zero exit code is not retried. It is a deterministic failure, such as a blocked publication, and it alerts at once.
- **Verified only by a real run:**
  - how `ecs:runTask.sync` reports a non-zero exit code (AWS's integration pages don't say; the checks above handle both cases)
  - the JSONata context fields `Execution.Name` and `State.RetryCount`
  - the task role's database user name
  - Redshift traffic through the private address

  The date logic was checked with `TestState`: 2026-03-03T11:00Z closes 2026-03-02, and 2026-07-01T10:00Z closes 2026-06-30.
- **The private address can change.** The workgroup's ENI address is read at apply time, and a workgroup change can move it, which needs another apply of the batch stack.

## Revisit when

- **The dashboards need fresher data than daily**, or a step can't fit Fargate's limits.
- **A second environment needs the DAG,** which would favour one orchestrator again.
- **MWAA's pricing or the budget changes** enough to make running the same DAG in AWS worth its cost.
