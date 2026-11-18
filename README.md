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

Python 3.12+, SQL, dbt Core, Apache Airflow, PostgreSQL (local), Amazon S3 and Redshift Serverless (cloud), FastAPI, Streamlit, MLflow, Docker, Terraform, and GitHub Actions. The reasoning is in [`docs/adr/`](docs/adr/).

## Documentation

Start with [`docs/README.md`](docs/README.md). Contributors should read [`CONTRIBUTING.md`](CONTRIBUTING.md) first. It contains the working rules and the definition of done.

## Getting started

There is nothing to run yet. Setup instructions will be added here once the Phase 1 engineering foundation (local stack, Makefile, CI) exists.
