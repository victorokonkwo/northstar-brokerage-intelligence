# Functional requirements

| | |
|---|---|
| Status | Approved 2026-11-18. **Nothing is implemented yet.** |
| Last updated | 2026-11-18 |

Requirement IDs (`FR-<module>-<nn>`) are stable. Reference them in PRs and tests. Each requirement lists its acceptance criteria (AC). A requirement is satisfied only when its AC are covered by automated tests or by a documented, reproducible check.

Non-functional requirements are in [non-functional-requirements.md](../architecture/non-functional-requirements.md).

---

## Shared: synthetic brokerage (SIM)

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-SIM-01 | Generate accounts, instruments, deposits and withdrawals, orders, executions (including partial fills and cancellations), fees, FX conversions, settlements, margin borrowing, interest accrual, cash dividends (roadmap 5C-a), and option exercise and expiry (roadmap 5C-b). | Generation is deterministic for a given seed and size. The same seed produces byte-identical output files. |
| FR-SIM-02 | Produce **canonical** events (ground truth) separately from **external** records (clearing, custody, bank statements, fee records). | External records are derived from canonical events through a separate, documented transformation, never by reading internal derived tables. |
| FR-SIM-03 | Inject configurable defects into external records: dropped records, duplicate clearing messages, wrong fees, delayed settlements, wrong FX conversions, stale prices. | Each injected defect is written to a defect manifest (record ID, defect type, expected financial impact) that serves as test ground truth. |
| FR-SIM-04 | Use settlement and trading calendars that include Canadian and US market holidays. | Settlement dates skip weekends and holidays. The calendar source is recorded in `docs/sources.md`. |
| FR-SIM-05 | Model treasury-relevant patterns: month-end withdrawals, seasonal deposits, and activity that varies with market volatility. | The patterns are parameterized in config and documented. They are visible in exploratory analysis of the generated data. |
| FR-SIM-06 | Support dataset sizes from a small dev seed up to a load-test dataset of at least 1M executions. | Generation at load-test size completes, and its runtime and memory are recorded in the performance results (measured, not estimated). |

## M1 — Data Foundation

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-M1-01 | Ingest source files into an append-only raw layer (local S3-compatible store; S3 in cloud), partitioned by event date. | Raw objects are never overwritten. Each load writes an ingestion manifest (run ID, files, row counts, checksums, schema version). |
| FR-M1-02 | Validate each record against its versioned event schema. Invalid records go to quarantine with a reason. | Quarantined records are retained and counted. A load fails loudly if the quarantine rate exceeds a configured threshold. |
| FR-M1-03 | Deduplicate events by `event_id`. Replaying any load is idempotent. | Ingesting the same input twice yields identical warehouse row counts and contents (contract test). |
| FR-M1-04 | Build dbt staging, intermediate, and core models for accounts, instruments, orders, executions, journal lines, cash movements, settlements, positions, prices, and FX. | Every model documents its grain and primary key. Keys are tested for `unique` and `not_null`. Core models have enforced contracts. |
| FR-M1-05 | Keep account history as SCD Type 2, built from the account events by event time (owner decision 2026-11-19: not a dbt snapshot, which would date versions by load time and lose versions loaded together). | A point-in-time join returns the account attributes valid at the event timestamp. |
| FR-M1-06 | Handle late-arriving and corrected records without rewriting published history ([ADR 006](../adr/006-bitemporal-records-and-restatements.md)). | A late record for a published date creates a new snapshot version with a restatement reason. The prior version can still be queried. |
| FR-M1-07 | Orchestrate daily runs with Airflow, with explicit dependencies, retries, failure alerts, and backfill. | A failed task is retried and then alerted. A backfill of N dates gives the same result as N daily runs. |
| FR-M1-08 | Run financial integrity tests in dbt and Python (domain model §8). | Every invariant in domain model §8 has at least one automated test, mapped in that section. |

## M2 — Financial Controls

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-M2-01 | Post every business event to the ledger through versioned posting rules. | Every posting rule has a test showing that it balances per currency for representative and edge-case inputs (property tests). |
| FR-M2-02 | Reconcile internal executions against clearing confirmations, cash against bank statements, settlement obligations against settlement confirmations, positions against custody snapshots, and fees against external fee records. | Each reconciliation compares two independently produced sources (FR-SIM-02). |
| FR-M2-03 | Match in stages: exact on stable IDs, then deterministic composite keys, then tolerance-based matching within configured thresholds. | Each match records the rule and rule version that produced it. Tolerance thresholds live in config. |
| FR-M2-04 | Classify exceptions by type and severity, and compute their financial impact in original currency and CAD. | 100% of the defects in the defect manifest are detected and correctly classified on the controlled test dataset. |
| FR-M2-05 | Persist an exception lifecycle: `OPEN → INVESTIGATING → RESOLVED`, plus `ESCALATED` (domain model §7). | Every state transition is an append-only audit record (actor, timestamp, reason). Illegal transitions are rejected. |
| FR-M2-06 | Auto-resolve only under named, versioned rules. Manual overrides require a reason. | No exception reaches `RESOLVED` without either an auto-resolution rule ID or an override reason and actor. |
| FR-M2-07 | Reconciliation runs are rerunnable and deterministic. | Rerunning for the same inputs and rule versions yields identical match results and exceptions (no duplicates). |
| FR-M2-08 | Enforce the publication gate: a daily snapshot cannot be marked published while blocking exceptions are open. | Test: an open blocking exception prevents publication, and resolving it allows publication. |
| FR-M2-09 | Provide an operations dashboard showing unmatched items, exposure, aging, and resolution status (KPI-02 to KPI-06). | The dashboard reads only published marts. Its figures equal the mart query results. |

## M3 — Market Risk

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-M3-01 | Compute account equity, margin requirement, loan value, and margin excess or deficit from versioned policy config. | Reference accounts reproduce hand-calculated values. Each result records its policy version. |
| FR-M3-02 | Apply configurable collateral haircuts by asset class or instrument. | Changing the config changes the results without code changes. The version is recorded. |
| FR-M3-03 | Historical-simulation VaR (99%, 1-day) and Expected Shortfall (97.5%) per account and in aggregate. | Reference cases pass. ES ≥ VaR property. The methodology doc states the window, the scenario construction, and the limitations. |
| FR-M3-04 | Versioned, reproducible stress scenarios: uniform equity shocks, single-name shocks, and volatility shocks for options. | A scenario definition plus input snapshot reproduces identical results. |
| FR-M3-05 | Price options and compute Greeks: Black–Scholes for European exercise only. For American-style options, use an appropriate method (e.g., a binomial tree) or a documented market-price approximation. | Black–Scholes matches published reference values and put–call parity. The method chosen for American options is documented with its limitations. |
| FR-M3-06 | Monitor concentration and risk limits, and raise alerts. | Alerts distinguish **calculation failure**, **missing or stale data**, and **genuine exposure increase**, as separate alert types. |
| FR-M3-07 | Backtest VaR (KPI-14). | Kupiec and Christoffersen statistics match reference implementations on fixed inputs. |
| FR-M3-08 | Every alert can be explained by drilling down to positions, prices, policy version, and model assumptions. | The alert record links to the input snapshot, policy version, and model version. |

**Regulatory note.** The margin engine is an *illustrative simulation*. CIRO's margin rules (Series 5000 of the Investment Dealer and Partially Consolidated Rules; see [sources](../sources.md)) are domain reference only. No output may be described as a compliant margin calculation.

## M4 — Treasury Intelligence

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-M4-01 | Separate known obligations (scheduled settlements) from uncertain flows (deposits and withdrawals). | Known obligations come from the warehouse deterministically. Only uncertain flows are modeled. |
| FR-M4-02 | Forecast uncertain flows at 1-, 7-, and 30-business-day horizons, per currency, with P10, P50, and P90 quantiles. | Every forecast row has a model version, training cutoff, run ID, horizon, and quantile. |
| FR-M4-03 | Compare a seasonal-naive baseline, a statistical model (e.g., SARIMAX), and a gradient-boosted quantile model. | All models are evaluated with the same walk-forward protocol and the results are reported, including when a model fails to beat the baseline. |
| FR-M4-04 | Use rolling-origin validation with no leakage. | A test asserts that every feature for a forecast origin uses only data with timestamps before the origin. |
| FR-M4-05 | Run liquidity stress scenarios: withdrawal spikes, volatility-driven volume, and large settlement days. | The scenarios are versioned config and their results are reproducible. |
| FR-M4-06 | Track experiments and models in MLflow. | A published forecast resolves to an MLflow run with parameters, metrics, and artifacts. |
| FR-M4-07 | Monitor drift and forecast error. | Drift and error metrics are computed daily, with thresholds in config. |

## M5 — Finance Intelligence

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-M5-01 | Provide a profitability mart covering revenue (commission, FX, margin interest), direct costs (clearing, execution, funding), and contribution margin by product (KPI-18). | Product revenue ties to the total of the ledger revenue accounts for each period (tie-out test). |
| FR-M5-02 | Use a documented, versioned cost-allocation method for indirect costs. | Allocated costs are reported separately from contribution margin. The method version is recorded on each row. |
| FR-M5-03 | Support point-in-time KPI restatements. | Querying "as published on date X" returns the version visible at X. |
| FR-M5-04 | Drill down from any dashboard aggregate to journal lines and source events. | For a sampled KPI value, the contributing journal lines sum exactly to the value. |
| FR-M5-05 | Produce a simulated reporting package: versioned report definitions, as-of dates, completeness checks, supporting evidence, and an audit trail. | Every report is labeled "SIMULATED — NOT FOR REGULATORY SUBMISSION". Completeness checks must pass before generation. |
| FR-M5-06 | Provide an executive dashboard covering revenue, costs, contribution margin, risk exposure summary, and reporting status. | Reads published marts only, with no calculations in the dashboard code. |

## Delivery interfaces

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FR-API-01 | A read-only FastAPI service exposes published results for reconciliation, risk, treasury, and finance. | OpenAPI spec committed. Contract tests. No business calculations in route handlers. |
| FR-DASH-01 | Four dashboards in Looker Studio (owner decision 2026-11-20, replacing Streamlit; [ADR 011](../adr/011-looker-studio-dashboards.md)): Brokerage Operations, Market Risk, Treasury, Executive Finance. | Each page shows its data's snapshot version and as-of time. |
