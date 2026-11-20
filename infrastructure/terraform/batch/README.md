# Terraform batch stack

| | |
|---|---|
| Status | **Applied 2026-11-20** after owner approval (20 resources, then a one-change fix to the state machine). The image of `75057f0` is in ECR. A non-business-day run skips correctly; a full close waits for Redshift's daily usage limit and the task user's grant. Each apply needs the owner's approval of its plan |
| Region | `ca-central-1`, as the data stack |
| Depends on | the data stack: its VPC, security group, workgroup, bronze bucket, and alerts topic, looked up by name. Apply the data stack's 4G changes first |
| Decision | [ADR 010](../../../docs/adr/010-cloud-batch-runtime.md): EventBridge Scheduler, Step Functions, and ECS Fargate |

It runs the daily close on Redshift: the Airflow DAG's steps as Fargate tasks, from one image. Facts and prices are in [`docs/sources.md`](../../../docs/sources.md).

| Resource | Settings |
|---|---|
| `aws_ecr_repository.batch` (`northstar-batch`) | the batch image. Tags are Git commits and immutable; images are scanned on push; the 10 most recent are kept |
| `aws_ecs_cluster.batch`, `aws_ecs_task_definition.batch` | Fargate, linux/arm64, 1 vCPU and 2 GB by default. The container's environment holds the warehouse connection, using the workgroup's private VPC address, and the dev schemas. No secrets |
| `aws_cloudwatch_log_group.tasks` (`/northstar/batch`) | task logs, kept 30 days |
| `aws_security_group.task` and two rules | no inbound; outbound HTTPS anywhere and port 5439 to the workgroup only. The workgroup's security group gains one rule: 5439 from this group |
| `aws_iam_role.execution` | pull the image, write logs |
| `aws_iam_role.task` | `GetCredentials` and `GetWorkgroup` on the workgroup; read and write the bronze prefixes `raw/`, `quarantine/`, `audit/`, `_stage/` |
| `aws_iam_role.states` | the ECS integration's documented permissions, passing the two roles to ECS only, and publishing alerts |
| `aws_sfn_state_machine.daily_close` | the daily close (`state_machine.json.tftpl`): seven tasks, each followed by an exit-code check. Any failure emails the step and the business date |
| `aws_scheduler_schedule.daily_close` and `aws_iam_role.scheduler` | 06:00 America/Toronto, closing the previous day. **Disabled** unless `schedule_enabled = true` |
| `aws_cloudwatch_metric_alarm.daily_close_failed` | failed or timed-out executions, to the alerts topic |

## Order of operations

1. **The data stack's 4G changes:**
   - **What they add:** the alerts topic and its email subscription, the monthly-compute alarm, and the `_stage/` expiry.
   - **How:** `make tf-plan-data`, then apply after approval. Confirm the subscription from the email AWS sends.
2. **This stack:** `make tf-plan-batch`, then apply after approval. The task definition names the image of `HEAD`.
3. **The image:** `make batch-image` builds that commit's image and pushes it.
4. **The database user:** the task role's user gets the pipeline role, once, from the admin with `northstar redshift-bootstrap --grant-to <user>`. The user's exact name is confirmed by the first run.
5. **A run:**

   ```
   aws stepfunctions start-execution --state-machine-arn <state_machine_arn> \
     --input '{"business_date": "2026-03-02"}'
   ```

## Cost (list prices, not a bill)

| Item | Price | Per run or month |
|---|---|---|
| Fargate, ARM | USD 0.03565 per vCPU-hour, 0.00389 per GB-hour | about USD 0.02 for 30 task-minutes at 1 vCPU and 2 GB |
| Public IPv4, while a task runs | USD 0.005 per address-hour | under USD 0.01 a run |
| Step Functions | USD 0.000025 per state transition | about 20 transitions a run |
| Scheduler, ECR, logs, alarm | see ADR 010 | cents a month |
| Redshift compute | the data stack's limits | the largest part, measured by the first run |
