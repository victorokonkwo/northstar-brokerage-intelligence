# Northstar Brokerage Intelligence

This repository is meant to become a single, auditable finance and market-risk analytics platform for a simulated Canadian online brokerage. The goal is to trace one client trade from ingestion through the accounting ledger, reconciliation, margin and portfolio risk, the treasury liquidity forecast, and profitability reporting.

> **Status: Phase 3 (data foundation) complete; Phase 4 (reconciliation and controls) is next.** Data is ingested into an append-only raw layer, modeled in dbt into daily cash, position, execution, and finance marts that tie out to the ledger, published as versioned snapshots that restate instead of overwriting, and orchestrated by Airflow with lineage. A seeded simulator produces a CAD/USD brokerage dataset: equities, options, FX, margin loans and interest, settlements, external records from clearing, bank and custody, and labelled defects. The double-entry ledger balances across all of it, in Python and in dbt. Everything runs on a local stack with CI and an AWS bootstrap. See [`docs/project/roadmap.md`](docs/project/roadmap.md) for what exists and what is planned.

> **Disclaimer.** This is an independent portfolio project and is not affiliated with Wealthsimple or any other firm. All client and transaction data is synthetic. Margin calculations and regulatory reports are illustrative simulations, not compliance calculations, and must not be used for real financial decisions or regulatory submissions.

## What it will do

| Module | Capability |
|---|---|
| M1 Data Foundation | Ingest brokerage, ledger, clearing, and market data into an S3 raw layer, then build tested dbt finance and risk marts |
| M2 Financial Controls | Reconcile internal executions, cash, settlements, positions, and fees against independently simulated external records, with an audited exception workflow |
| M3 Market Risk | Margin and collateral, historical VaR and Expected Shortfall, stress testing, concentration, and option Greeks |
| M4 Treasury Intelligence | 1-, 7-, and 30-day net cash-flow forecasts with prediction intervals and withdrawal-stress scenarios |
| M5 Finance Intelligence | Product profitability, unit economics, KPI restatements, and a simulated reporting package with lineage to source transactions |

The flagship scenario is a client who deposits CAD 25,000, buys US-listed securities, borrows on margin, and trades an option. It will be the end-to-end integration test and the demo. See [`docs/business/project-charter.md`](docs/business/project-charter.md) §4.

## Architecture on AWS

![The Northstar data platform on AWS: the operator or a schedule starts the daily close in Step Functions, which runs batch tasks on ECS Fargate; they ingest into the S3 bronze bucket, Redshift Serverless copies the data in, and dbt, reconciliation, and publication run there; alarms and the budget email the operator](docs/architecture/aws/data-platform.svg)

Each run closes one business day:

1. **Start.** The operator starts a run with a business date. A daily 06:00 (Toronto) schedule exists but is off, because the dev dataset covers Q1 2026 only.
2. **Orchestrate.** A Step Functions state machine runs seven steps in order, each as a container on ECS Fargate: check the business day, ingest and load, dbt build, reconcile, dbt controls, publish the controls, and publish. Every step runs the same image from ECR. The tasks run inside the warehouse VPC, and nothing can connect to them.
3. **Ingest (1).** The task generates the seeded, synthetic dataset and writes it to the S3 bronze bucket: valid records under `raw/`, rejected ones under `quarantine/`, and manifests and lineage under `audit/`. Objects are content-addressed and never overwritten.
4. **Load (2).** New rows are staged as JSON Lines under `_stage/`, and Redshift copies them into the `raw` and `ledger` schemas. Replaying a run inserts nothing.
5. **Transform, reconcile, publish (3).** On Redshift Serverless, dbt builds the silver and gold models. Reconciliation matches internal records against the simulated clearing, bank, and custody records. Publication writes a day's versioned snapshot only when its controls pass, so a day with an open critical or high exception stays unpublished until someone resolves it.
6. **Read.** Looker Studio dashboards will read the published schemas (planned, roadmap 4H). Redshift accepts connections on port 5439, over TLS, from Looker Studio and the operator only.
7. **Watch the cost and failures.** A failed step, a failed or timed-out run, or the monthly compute limit (60 RPU-hours) emails the operator through SNS. A daily cap of 4 RPU-hours turns Redshift queries off, and a USD 10 monthly AWS budget emails when actual spend passes 80% of it or the forecast passes 100%.

Three Terraform stacks define all of it, with their state in S3: `bootstrap` (budget, state bucket), `data` (VPC, bronze bucket, Redshift, alerts), and `batch` (the daily close). `make diagrams` redraws this picture from them with [TerraVision](https://github.com/patrickchugh/terravision). The per-stack diagrams and what they leave out are in [the cloud warehouse doc](docs/architecture/cloud-warehouse.md#architecture), and the decisions in [ADR 005](docs/adr/005-local-postgres-cloud-redshift.md) and [ADR 010](docs/adr/010-cloud-batch-runtime.md). Locally, the same pipeline runs on Docker Compose with Postgres, SeaweedFS, and Airflow (see [Local stack](#local-stack)).

## Planned stack

Python 3.13, SQL, dbt Core, Apache Airflow, OpenLineage, OpenTelemetry, PostgreSQL (local), Amazon S3, Redshift Serverless, and CloudWatch (cloud), FastAPI, Streamlit, MLflow, Docker, Terraform, and GitHub Actions. The reasoning is in [`docs/adr/`](docs/adr/).

## Documentation

Start with [`docs/README.md`](docs/README.md). Contributors should read [`CONTRIBUTING.md`](CONTRIBUTING.md) first. It contains the working rules and the definition of done.

## Getting started

Prerequisites: [uv](https://docs.astral.sh/uv/) and `make`. uv installs the pinned Python (3.13) and every tool from `uv.lock`.

```bash
make setup   # install the locked environment and the git hooks
make check   # lint, import-layer contracts, mypy --strict, tests
make help    # list every target
```

### Local stack

The stack needs Docker with Compose v2. It runs the warehouse Postgres, Airflow 3.4.0, and a SeaweedFS S3-compatible store, all bound to `127.0.0.1`.

```bash
make env     # create .env with generated local secrets (never overwrites)
make up      # start everything and wait until each service is healthy
make ps      # service status
make down    # stop (data volumes are kept)
make reset   # stop and DELETE the data volumes; needed if .env is regenerated

make simulate           # seeded dev dataset (50 accounts, Q1 2026): events, external records, defects
make ingest             # validate it into the raw layer of the object store (needs make up)
make simulate-load      # ingest, load into raw_dev/ledger_dev, dbt build, KPI-01 (needs make up)
make reconcile          # reconcile the dev dataset, verify against its defect manifest, build the controls marts
make publish            # publish controls, then financial snapshots; open blocking exceptions hold a date as DRAFT
# Airflow (http://localhost:8080) runs the same daily close as the DAG northstar_daily
make test-integration   # loader tests against the running warehouse
make dbt-build          # build and test the dbt models
make dbt-build-full     # full-refresh build of the dev dataset, then compare-incremental checks the incremental one equals it
make sql-lint           # SQLFluff (needs the running warehouse)

# Cloud (S3 bronze, Redshift Serverless; needs `aws login` and the applied Terraform data stack)
make redshift-bootstrap # as the admin: append-only tables owned by the admin, pipeline granted SELECT/INSERT
make dbt-build-redshift # dbt build on Redshift
make compare-warehouses # every dbt model, Postgres vs Redshift, row for row
make test-redshift      # live loading, append-only, dbt-check, and incremental controls (minutes of compute)
make parity             # the same pipeline on both warehouses from nothing, every table compared
# The cloud run (ADR 010): make tf-plan-batch, make batch-image, then start the state machine
```

See [cloud warehouse](docs/architecture/cloud-warehouse.md).

| Service | Address | Credentials (in `.env`) |
|---|---|---|
| Airflow UI and API | http://localhost:8080 | `AIRFLOW_ADMIN_USERNAME` / `AIRFLOW_ADMIN_PASSWORD` |
| Warehouse Postgres | `localhost:5432`, database `northstar` | `NBI_PG_USER` / `NBI_PG_PASSWORD` |
| S3 endpoint | http://localhost:8333, bucket `northstar-data-local` | `NBI_S3_ACCESS_KEY_ID` / `NBI_S3_SECRET_ACCESS_KEY` |

The ports can be changed in `.env`. The stack also runs an OpenTelemetry Collector (`127.0.0.1:4318`). With `OTEL_EXPORTER_OTLP_ENDPOINT` set in `.env`, the `northstar` command exports traces, metrics, and logs to it. You can see them with `docker compose --env-file .env -f infrastructure/docker/compose.yaml logs otel-collector`. An `.env` created before this was added needs the four `OTEL_*` and `NBI_ENVIRONMENT` lines from `.env.example`.

### Sprint 1 slice, end to end

With the stack up (`make up`), one command simulates a client depositing cash and buying a synthetic TSX stock, posts the double-entry journal, loads it into the warehouse, builds and tests the dbt models, and prints the KPI-01 ledger control:

```bash
make e2e
```

Output on the committed scenario (seed 42; illustrative values; produced by `make e2e` with fee schedule 2026.2, which adds clearing fees, 2026-11-19):

```
business_date currency batches lines total_debit total_credit unbalanced(KPI-01)
2026-03-02 CAD 1 2 25000.00 25000.00 0
2026-03-03 CAD 2 10 5249.50 5249.50 0
2026-03-04 CAD 1 2 5238.60 5238.60 0
KPI-01 passed: every journal batch balances
```

Running it again loads nothing new and leaves the results unchanged. CI checks that on every pull request.

### Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every pull request and every push to `main`. It runs `make check`, the repository hygiene hooks, and a dependency vulnerability audit (`uv audit`). It then starts the local stack from a fresh checkout and runs the integration tests, `dbt debug`, `dbt parse`, SQLFluff, and `make e2e` twice, checking that the results are identical.
