# Engineering standards

| | |
|---|---|
| Status | Adopted for all new code. The tooling is configured in Phase 1; until then these are the rules to configure it against. |
| Last updated | 2026-11-18 |

The working rules (honesty, financial correctness, boundaries) are in [CONTRIBUTING.md](../../CONTRIBUTING.md#working-rules). This document covers *how code is written*.

## 1. Python

### 1.1 Toolchain

| Concern | Tool | Setting |
|---|---|---|
| Version | CPython 3.13 (Sprint 1 decision, 2026-11-18) | `.python-version`, `requires-python` |
| Packaging | `uv`, `pyproject.toml`, committed `uv.lock` | src layout: `src/northstar/` |
| Lint and format | Ruff | lint and formatter, one config in `pyproject.toml` |
| Types | mypy | `--strict` for `src/` and `ml/`. No `Any` in public signatures. |
| Tests | pytest, Hypothesis | see §4 |
| Hooks | pre-commit | Ruff, mypy, SQLFluff, secret scan, end-of-file fixers |

### 1.2 Code style

- **Names carry financial meaning.** Use `gross_amount_usd` or `margin_excess`, not `amt` or `val`. Units and currency appear in the name or the type.
- Functions do one thing and stay small. Prefer pure functions in `domain/`.
- Use immutable value objects (`@dataclass(frozen=True, slots=True)` or Pydantic models with `frozen=True`) for domain entities and events.
- Use `enum.StrEnum` for closed sets such as exception types, statuses, and sides. Never use magic strings.
- Every public function and class gets a docstring stating its financial meaning, units, sign conventions, and the exceptions it raises.
- No mutable default arguments, no module-level mutable state, no global RNG.
- Standard library first. A new dependency needs a reason in the PR (see CONTRIBUTING).

### 1.3 Errors

- Define a domain exception hierarchy in `northstar.common.exceptions`, e.g., `UnbalancedJournalError`, `CurrencyMismatchError`, `StalePriceError`, `PolicyNotFoundError`.
- Raise specific exceptions. No bare `except:`. `except Exception` is allowed only at process boundaries (job entrypoints), where the error is logged with context and re-raised or turned into a failed run status.
- Never return `None` or a default to hide a failure. Make failures explicit in the type (`X | None`, documented) or raise.
- Validate at the boundaries (ingestion, config loading, API input). Inside the domain, trust the types.

### 1.4 Configuration

- **Settings** (environment-specific, non-secret): typed settings objects loaded from `config/<env>.yaml` plus environment variables.
- **Policies** (margin rates, haircuts, limits, fee schedules, tolerances, stress scenarios): versioned YAML under `config/<domain>/`, loaded into validated, typed models. Each file has a `version` and `effective_from`. Every result records the policy version it used.
- **Secrets** come only from environment variables or Secrets Manager. They are never in YAML.
- The domain layer receives config objects as arguments. It never reads files or the environment itself.

### 1.5 Logging and observability

- Structured (JSON) logs. Every log line in a job carries `run_id`, `business_date`, and the job name.
- Log events and decisions, such as "quarantined 12 records: schema_error". Never log secrets or full payloads containing personal-like fields, even synthetic ones.
- Use OpenTelemetry for traces, metrics, and logs. Metrics (row counts, durations, quarantine rate, exception counts) are emitted by every job, so pipeline health can be monitored.
- In AWS, export OpenTelemetry data to CloudWatch over OTLP. Keep telemetry configuration out of the domain layer; see [ADR 008](../adr/008-data-lineage-and-observability.md).
- Emit OpenLineage events for supported Airflow and dbt jobs to record pipeline-level job, run, and dataset lineage. The event backend remains to be selected before integration.

## 2. Orchestration (Airflow)

- DAG files declare structure only. Tasks call functions in `src/northstar/application/`. No business logic in DAG files, and no heavy work at import time.
- Tasks are idempotent and keyed by the logical (business) date. Rerunning a task for a date replaces only that date's outputs, under the versioning rules of ADR 006.
- Every DAG has retries with backoff, a failure callback that alerts, and documented dependencies.
- Backfills use the same code path as daily runs.

## 3. SQL and dbt

### 3.1 Project

- The project was created with **`dbt init`** at `warehouse/dbt/` ([README](../../warehouse/dbt/README.md)). New dbt projects or packages are also created with `dbt init`. Generated example models are deleted, not committed.
- dbt Core 1.12.x, dbt-postgres locally, dbt-redshift in the cloud ([ADR 005](../adr/005-local-postgres-cloud-redshift.md)).
- Lint with SQLFluff using the dbt templater, with the dialect set for the target.

### 3.2 Layers and naming

| Layer | Folder | Naming | Rules | Default materialization |
|---|---|---|---|---|
| Sources | `models/sources/` | `<source>.yml` | declare sources with freshness checks | n/a |
| Staging | `models/staging/<source>/` | `stg_<source>__<entity>` | 1:1 with a source table; rename, cast, and standardize only; no joins | view |
| Intermediate | `models/intermediate/` | `int_<entity>_<verb>` | reusable business logic; not exposed to consumers | ephemeral or view |
| Core | `models/core/` | `dim_<entity>`, `fct_<entity>` | canonical model (domain model §3); **contracts enforced** | table or incremental |
| Marts | `models/marts/<domain>/` | `mart_<subject>` | consumer-facing; **contracts enforced**; carries snapshot version | table |

Model folders are created when their first real model is added, not before.

### 3.3 Model rules

- Each model's YAML documents its **grain** and **primary key**. The key has `unique` and `not_null` tests. Composite keys are tested with a uniqueness test on the combination.
- Foreign keys have `relationships` tests.
- Monetary, price, quantity, and rate columns use the `numeric` precisions from ADR 004. **Never use `float`.** Every amount column has an adjacent currency column.
- Use CTEs: import CTEs first, then logical CTEs, then a final `select`. No `select *` in core or marts.
- Incremental models state their `unique_key`, their strategy, and how late-arriving data is handled. Published history is never merged over (ADR 006).
- Dialect differences go through macros.
- Financial invariants are singular tests in `tests/` named `assert_<invariant>.sql` (domain model §8).
- Every mart row carries lineage columns (domain model §4.3).

## 4. Testing strategy

| Suite | Location (target) | What it proves | Runs |
|---|---|---|---|
| Unit | `tests/unit/<domain>/` | individual functions on hand-computed cases | every commit |
| Financial invariants | `tests/financial_invariants/` | the domain model §8 invariants, as Hypothesis property tests over generated inputs | every commit |
| Quantitative validation | `tests/quantitative_validation/` | pricing, VaR/ES, margin, and stress against reference values and identities (e.g., put–call parity, ES ≥ VaR) | every commit |
| Contract | `tests/contract/` | event schemas, warehouse contracts, API schemas | every commit |
| dbt tests | `warehouse/dbt/tests/` + YAML | data tests on fixtures | every PR (CI Postgres) |
| Integration | `tests/integration/` | ingestion replay idempotency, warehouse builds, API against a test database | every PR |
| End to end | `tests/end_to_end/` | the flagship scenario, from deposit through report | every PR (small seed) |
| Performance | `tests/performance/` | load-test benchmarks; results recorded, not asserted against invented thresholds | on demand / release |

Rules:

- **A test must be able to fail.** When you write one, check it fails against a deliberately broken implementation (mutation by hand), at least for invariant tests.
- Unit tests make no network calls and do no wall-clock-dependent work. External APIs use recorded fixtures.
- Fixtures are small, deterministic, and documented. Each file under `tests/fixtures/` has a short note saying how it was produced.
- Tests are not skipped without a reason that links to a roadmap item.
- Coverage is reported but is not a target (NFR-20). Invariant and posting-rule coverage is the gate.

## 5. Machine learning

- Use temporal splits only, with walk-forward or rolling origin. Write a leakage test for every feature pipeline.
- Every model is compared against a seasonal-naive baseline using identical splits and metrics.
- Seeds are explicit. Experiments are tracked in MLflow: parameters, metrics, data version, and code version.
- Every model in production use has a model card stating its purpose, training window, validation, known failure modes, monitored drift, and the decisions it supports.
- Notebooks are for exploration only. Logic that a pipeline needs moves into `ml/` or `src/` with tests.

## 6. Documentation

- Every doc has a status header (implemented, planned, or draft) and a last-updated date.
- Docs describe what exists. Anything planned is labeled as planned. Measured numbers carry their provenance (CONTRIBUTING.md, W1 rule 5).
- A changed decision gets a new ADR, never a silent edit.
- External facts go into [sources.md](../sources.md) with a verification status.

## 7. Git and pull requests

- Conventional Commits. Small PRs. Refactors are separate from behavior changes.
- The PR description states what changed, why, the requirement or KPI IDs affected, the test evidence (commands and their results), and any doc updates.
- `main` is always green. CI must pass before merge.
