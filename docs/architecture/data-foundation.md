# Data foundation

| | |
|---|---|
| Status | Partly implemented (Phase 3): ingestion and the warehouse load (3B); dbt staging, dimensions, and core trading facts (3C). Daily facts, marts, published snapshots, and orchestration follow in 3D–3G ([roadmap](../project/roadmap.md#phase-3-weeks-57)). |
| Requirements | FR-M1-01 to FR-M1-05 ([requirements](../business/requirements.md)) |

## Ingestion (FR-M1-01 to FR-M1-03)

`northstar ingest` (`make ingest`) reads a dataset that `make simulate` generated and writes it to the object store. It never writes to the warehouse.

```text
canonical/events.jsonl, external/<set>.jsonl
  → validate each line against its versioned model
  → accepted lines: raw/<domain>/<entity>/event_date=YYYY-MM-DD/part-<sha256>.jsonl
  → rejected lines: quarantine/{schema_errors,business_rule_failures}/part-<sha256>.jsonl
  → audit/ingestion_manifests/<run_id>.json
```

**Validation.** Events are validated against `EVENT_MODELS` by `event_type`, and external records against `RECORD_MODELS` by record set. A line goes to quarantine with a reason, and is never repaired, when:
- it is not JSON
- its event type is unknown
- its `schema_version` is newer than the code supports (domain model §4.2)
- its payload fails validation (for example, an amount sent as a JSON number)
- it is an execution on a day its market does not trade (domain model §5)
- its ID appears in the input with different content

In that last case, every copy of the ID is quarantined, including identical ones. The run doesn't guess which version is right.

**Duplicates.** An exact repeat of an earlier line is counted in the manifest and stored once. Duplicates across runs are removed by the warehouse load: it inserts only new IDs, and refuses an ID whose stored content differs (`LoadConflictError`).

**Append-only.** Every object is written with `If-None-Match: *`, so the store itself refuses to overwrite one ([sources](../sources.md)). Raw and quarantine objects are named by the SHA-256 of their content. A replay of the same input therefore writes no data objects, only a manifest for the attempt.

**Raw is as received.** Accepted lines are stored byte for byte, in input order.

**Partitions.** Events are partitioned by the business date of `event_timestamp` (America/Toronto); external records by their own business date.

| Entity | Domain | Event date |
|---|---|---|
| `price_updated`, `fx_rate_published` | `market_data` | business date of the event |
| other canonical events | `brokerage` | business date of the event |
| `clearing_confirmations`, `fee_records` | `clearing` | `trade_date` |
| `settlement_confirmations` | `clearing` | `actual_settlement_date` |
| `bank_statement_lines` | `bank` | `value_date` |
| `fx_confirmations` | `bank` | `trade_date` |
| `custody_snapshots` | `custody` | `snapshot_date` |
| `price_feed` | `market_data` | `price_date` |

**Threshold.** `config/ingestion/quarantine_policy.yaml` (illustrative, versioned) sets the maximum share of a run's lines that may be quarantined: 0.5%. Above it, the run writes only its quarantine objects and a `FAILED` manifest, and raises `QuarantineThresholdError`. A failed run cannot be loaded.

**Manifest.** Each run's manifest records:
- the run ID, status, time, and code version (`NBI_CODE_VERSION`, or the Git commit with `-dirty` for local edits)
- the quarantine policy version
- each input file with its SHA-256 and line counts
- each object with its rows, SHA-256, and whether this run wrote it
- the schema versions seen
- the quarantine reasons and rate

## Warehouse load

`northstar load-run` reads a successful run's raw objects back and loads them in one transaction:
- events into `<raw>.brokerage_events`
- external records into `<raw>.external_records`
- journal lines, posted from the events with the v1 rules, into `<ledger>.journal_lines`

`make simulate-load` runs `ingest`, `load-run` into `raw_dev`/`ledger_dev`, and `dbt build`, then prints KPI-01. The Sprint 1 slice (`make e2e`) still loads directly.

On the dev dataset (seed 42), the first run accepts all 16,863 lines into 929 raw objects. A replay writes 0 objects, and loading it inserts 0 rows (`make simulate-load`, then `make ingest` and `northstar load-run` with a second run ID). CI checks both on every push.

## Warehouse models (FR-M1-04, FR-M1-05)

Staging models are 1:1 with an event type (`stg_brokerage__<event_type>`) or reference table, and only rename and cast. Core models have enforced contracts, a documented grain and key, `numeric` amounts with a currency column, and the audit columns of domain model §4.3.

| Model | Grain (primary key) | Built from |
|---|---|---|
| `dim_account` | account version (`account_sk`) | `account_created` and `account_updated`: SCD Type 2 by event time |
| `dim_instrument` | instrument version (`instrument_sk`) | `instrument_listed`; one version each, since no event changes an instrument yet |
| `dim_calendar` | market × date (`calendar_day_id`) | `raw.market_calendar_days`, written by the loaders from `northstar.domain.calendar` |
| `fct_orders` | order (`order_id`) | orders, their executions, and cancellations; status `OPEN`, `PARTIALLY_FILLED`, `FILLED`, or `CANCELLED` |
| `fct_executions` | execution (`execution_id`) | `trade_executed`, now with the clearing fee and execution time |
| `fct_settlements` | obligation (`settlement_id`) | executions grouped by obligation; `SETTLED` once `trade_settled` arrives, else `PENDING` with a null amount |
| `fct_cash_movements` | deposit, withdrawal, or conversion (`cash_movement_id`) | money in and money out, each with its currency; business date in America/Toronto |

**Account history is event-sourced, not a dbt snapshot.** FR-M1-05 names a dbt snapshot. A snapshot records the state it sees at each dbt run, so two updates loaded in one run would leave only the second, and a backfill would date versions by load time. `dim_account` is instead built from the account events: every version is valid from its event's timestamp (inclusive) to the next version's (exclusive). A point-in-time join therefore uses event time, and a replay rebuilds exactly the same history. The requirement's wording should be updated to match; that is pending owner confirmation.

**Calendars have one owner.** The trading and settlement flags and the neighbouring settlement days come from `northstar.domain.calendar`. The loaders write them to `raw.market_calendar_days`; dbt only exposes them. Neighbours outside a calendar's coverage are null, not guessed. A stored day that differs from the configuration is refused (`LoadConflictError`): correcting a calendar that results were computed with is a restatement, not an overwrite.

**Loaded rows are append-only in the database.** Triggers refuse `UPDATE`, `DELETE`, and `TRUNCATE` on `brokerage_events`, `external_records`, `market_calendar_days`, and `journal_lines` (INV-11, W2.4).

**Tests.**
- Column tests: keys are `unique` and `not_null`, foreign keys have `relationships` tests, and enumerations have `accepted_values`.
- Singular tests:
  - INV-06 settlement integrity: membership both ways, and quantity and amount sums
  - the FR-M1-05 point-in-time join: each order matches one account version, which was `ACTIVE`
  - contiguous SCD2 versions
  - every execution on a trading day and settling on a settlement day of its market
  - order quantities that reconcile
- dbt unit tests cover what the dev dataset never produces: open and partially filled orders, pending settlements, an account's version chain, and a Toronto business date before UTC midnight.

On the dev dataset (seed 42) the build has 126 nodes, and all pass:
- 56 account versions for 50 accounts
- 416 orders (398 filled, 18 cancelled)
- 416 settled obligations
- 274 cash movements

External record sets are loaded to `raw.external_records` but not yet modelled. Their staging models come with reconciliation in Phase 4, which consumes them.
