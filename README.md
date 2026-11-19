# Northstar Brokerage Intelligence

This repository is meant to become a single, auditable finance and market-risk analytics platform for a simulated Canadian online brokerage. The goal is to trace one client trade from ingestion through the accounting ledger, reconciliation, margin and portfolio risk, the treasury liquidity forecast, and profitability reporting.

> **Status: Phase 0 (design) complete; Phase 1 (engineering foundation) is next.** The repository has approved design documentation and an empty dbt project scaffold. No platform code has been written yet. See [`docs/project/roadmap.md`](docs/project/roadmap.md) for what exists and what is planned.

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

make test-integration   # loader tests against the running warehouse
make dbt-build          # build and test the dbt models
make sql-lint           # SQLFluff (needs the running warehouse)
```

| Service | Address | Credentials (in `.env`) |
|---|---|---|
| Airflow UI and API | http://localhost:8080 | `AIRFLOW_ADMIN_USERNAME` / `AIRFLOW_ADMIN_PASSWORD` |
| Warehouse Postgres | `localhost:5432`, database `northstar` | `NBI_PG_USER` / `NBI_PG_PASSWORD` |
| S3 endpoint | http://localhost:8333, bucket `northstar-data-local` | `NBI_S3_ACCESS_KEY_ID` / `NBI_S3_SECRET_ACCESS_KEY` |

The ports can be changed in `.env`.

### Sprint 1 slice, end to end

With the stack up (`make up`), one command simulates a client depositing cash and buying a synthetic TSX stock, posts the double-entry journal, loads it into the warehouse, builds and tests the dbt models, and prints the KPI-01 ledger control:

```bash
make e2e
```

Output on the committed scenario (seed 42; illustrative values; produced by `make e2e`, 2026-11-19):

```
business_date currency batches lines total_debit total_credit unbalanced(KPI-01)
2026-03-02 CAD 1 2 25000.00 25000.00 0
2026-03-03 CAD 2 6 5248.50 5248.50 0
2026-03-04 CAD 1 2 5238.60 5238.60 0
KPI-01 passed: every journal batch balances
```

Running it again loads nothing new and leaves the results unchanged. CI checks that on every pull request.

### Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every pull request and every push to `main`. It runs `make check`, the repository hygiene hooks, and a dependency vulnerability audit (`uv audit`). It then starts the local stack from a fresh checkout and runs the integration tests, `dbt debug`, `dbt parse`, SQLFluff, and `make e2e` twice, checking that the results are identical.
