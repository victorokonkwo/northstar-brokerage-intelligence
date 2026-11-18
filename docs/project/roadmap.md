# Roadmap and status

| | |
|---|---|
| Last updated | 2026-11-18 |
| Current phase | **Phase 1 — Engineering foundation (not started)**. Phase 0 done 2026-11-18. |

This is the **authoritative status tracker**. Update it in the same change that alters status. Status values are `Not started`, `In progress`, `Done`. "Done" means the exit gate passed, with evidence linked.

The plan is ambitious: 18 weeks for one developer with prior experience in the stack. If a gate fails, the schedule moves. The gate never does. **If the accounting or data-quality gates fail, no work proceeds on forecasts or dashboards built on that data.**

## Current state of the repository

| Item | State |
|---|---|
| Phase 0 documentation | Approved by owner 2026-11-18 |
| Phase 0 gate: KPI completeness | All 20 KPIs have a decision, definition, source, and verification method. Deferred parameters: KPI-09/10 window and quantile method → Phase 5 (OQ-08); KPI-16 `available_liquidity` → Phase 6 (OQ-06, owner decision 2026-11-18). **Complete** |
| Phase 0 gate: owner review | **Complete.** Domain model (§5 time rules, §7.3 severity, §9 chart of accounts and posting rules) and charter scope approved by owner 2026-11-18 |
| `warehouse/dbt/` | Scaffolded with `dbt init` (dbt Core 1.12.7, dbt-postgres 1.11.0); example models removed; `dbt parse` passes; **no models** |
| Python package, tests, CI, Docker Compose, Makefile, Terraform | Not started |
| Git repository | Initialized 2026-11-18 (branch `main`). Remote: [github.com/victorokonkwo/northstar-brokerage-intelligence](https://github.com/victorokonkwo/northstar-brokerage-intelligence) (public). Phase 0 docs submitted as the first PR |

## Phases

| Phase | Weeks | Scope | Exit gate | Status |
|---|---|---|---|---|
| 0 Business architecture | 1 | Charter, requirements, glossary, KPI dictionary, domain model, event contracts, NFRs, ADRs, risk register | **Architecture gate:** every KPI has a decision, an exact definition, an authoritative source, and a verification method; the owner has reviewed the domain model | Done (2026-11-18) |
| 1 Engineering foundation | 2 | Monorepo, `pyproject.toml` + `uv.lock`, Ruff, mypy, pytest, pre-commit, Docker Compose (Postgres, Airflow, S3-compatible store), CI, AWS budget alert + Terraform baseline | **Platform gate:** a fresh clone can start the stack, run a sample pipeline and the tests, and reproduce the same result | Not started |
| 2 Brokerage simulation + accounting | 3–4 | Deterministic generators, posting rules, ledger, settlement calendar, stock record, external-record simulators, defect injection | **Financial correctness gate:** the simulator produces a fully balanced ledger, traceable trades and positions, correct settlement obligations, and repeatable output | Not started |
| 3 Data foundation | 5–7 | Ingestion (validation, dedup, quarantine, manifests, backfill), dbt staging/core/marts, snapshots, data-quality tests, load-test benchmark | **Trusted data gate:** daily cash, position, execution, and finance marts pass quality tests; historical snapshots are reproducible | Not started |
| 4 Reconciliation + controls | 8–9 | Staged matching, exception classification and lifecycle, audit trail, publication gate, operations dashboard | **Operations gate:** all injected defect scenarios detected; source evidence unaltered; no resolution without an auditable rule or override | Not started |
| 5 Market risk | 10–12 | Margin and collateral, haircuts, VaR/ES, stress, options pricing and Greeks, limits and alerts, backtesting | **Model validation gate:** an analyst can explain any alert from inputs, policy, and model versions; reference cases and backtests pass | Not started |
| 6 Treasury forecasting | 13–14 | Known vs. uncertain flows, baseline/statistical/GBM models, walk-forward evaluation, intervals, scenarios, MLflow | **Forecast evaluation gate:** reproducible forecasts evaluated against the baseline, with error, bias, coverage, and stress results documented | Not started |
| 7 Finance intelligence | 15–16 | Profitability mart, allocation method, KPI restatements, drilldown, simulated reporting package, executive dashboard | **Reporting gate:** every finance KPI has a documented formula, passes tie-outs, and drills down to transactions | Not started |
| 8 Production readiness | 17–18 | Load tests, failure injection, recovery drill, access-control tests, cost review, runbooks, model cards, case study, demo | **Release gate:** a reviewer can reproduce the system, run the tests, and execute the flagship scenario without the author's help | Not started |

## Sprint 1 (weeks 1–2)

Goal: by the end of the sprint, one command creates a client, deposits cash, executes a trade, posts the journal entries, and verifies the journal balances.

| Day | Task | Status |
|---|---|---|
| 1 | Project charter; define the five modules | Done |
| 2 | Business metrics, accounting assumptions, reporting cutoffs | Done |
| 3 | Conceptual and logical entity models | Done |
| 4 | Event schemas, identifiers, financial invariants | Envelope, identifiers, and invariants done. JSON Schemas are written with the generators (Days 7–8) |
| 5 | System architecture and ADRs | Done |
| 6 | Initialize Git, Python project, Docker Compose, CI | Git initialized, remote created, dbt scaffold done; Python project, Docker Compose, CI not started |
| 7 | Account, instrument, and deposit event generators | Not started |
| 8 | Order/execution simulation and double-entry journal postings | Not started |
| 9 | Initial SQL models and financial integrity tests | Not started |
| 10 | Demonstrate one deposit → trade → ledger workflow end to end | Not started |

### Decisions needed in Sprint 1

| Decision | Options | Notes |
|---|---|---|
| Python version pin | 3.12 or 3.13 | dbt Core 1.12 was verified on 3.12. Check that statsmodels, LightGBM, Airflow, and MLflow support the chosen version before pinning. |
| Airflow major version | 2.x or 3.x | Check the provider packages and the Docker image for the chosen version. |
| Local S3-compatible store | e.g., MinIO, LocalStack | Check the licence and image availability. |
| Calendar source (OQ-04) | maintained YAML or `exchange_calendars` | Must cover CA and US holidays for the whole simulated period. |
| Import boundary enforcement | `import-linter` or similar | See ADR 001 consequences. |
