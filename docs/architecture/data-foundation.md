# Data foundation

| | |
|---|---|
| Status | Partly implemented (Phase 3): ingestion and the warehouse load (3B); dbt staging, dimensions, and core trading facts (3C); daily facts and the gate marts (3D). Published snapshots and orchestration follow in 3E–3G ([roadmap](../project/roadmap.md#phase-3-weeks-57)). |
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

**Account history is event-sourced, not a dbt snapshot.** FR-M1-05 names a dbt snapshot. A snapshot records the state it sees at each dbt run, so two updates loaded in one run would leave only the second, and a backfill would date versions by load time. `dim_account` is instead built from the account events: every version is valid from its event's timestamp (inclusive) to the next version's (exclusive). A point-in-time join therefore uses event time, and a replay rebuilds exactly the same history. FR-M1-05 was reworded to match (owner decision, 2026-11-19).

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

## Daily facts and marts

Daily models are reported for **reporting dates**: every ledger business day (an XTSE settlement day) from the first to the last posting date (`int_reporting_dates`).

| Model | Grain | What it holds |
|---|---|---|
| `fct_fx_rates_daily` | base × quote × rate date | each published USD/CAD reference rate (synthetic source) |
| `int_fx_rates_applicable` | calendar date | the rate that applies on each date under fallback policy v1 (the most recent rate on or before it), with its own date and `is_fallback` |
| `fct_market_prices_daily` | instrument × reporting date | the latest close on or before the date, and `is_stale` |
| `fct_positions_daily` | account × instrument × reporting date | trade-date and settled quantity, market value in the instrument currency and CAD, and the price and FX rate used |
| `mart_daily_client_cash` | account × currency × reporting date | opening balance, credits, debits, and closing balance from GL 2100 (negative = margin loan), closing in CAD |
| `mart_daily_positions` | account × reporting date | portfolio value in CAD, with missing-price and stale-price flags |
| `mart_daily_executions` | trade date × market × currency | execution and order counts, gross, commission, and clearing fee |
| `mart_daily_finance` | posting date × currency | commission, FX conversion, and margin interest revenue, clearing costs, net contribution, and net contribution in CAD |

**Amounts come from the ledger (ADR 002, ADR 007).**
- Cash, revenue, and cost amounts are sums of journal lines.
- An execution's gross amount, commission, and clearing fee are read from the lines Python posted for it (`int_execution_amounts`). The 2200 credit of a buy includes the clearing fee, which is subtracted, and a sell's gross is its 1200 debit.
- The only calculations in SQL are reporting ones: positions times price, and translation to CAD.

**Rounding is half-even, once (ADR 004).** Postgres `round()` rounds halves away from zero, so reporting outputs use the `round_half_even` macro. A market value is rounded when written to `fct_positions_daily`, and the positions mart sums those values. The INV-06 test, which recomputes gross amounts to check Python's postings, uses the same macro.

**A stale price is flagged, never hidden (INV-08).**
- **Stale:** a price is stale when its market traded after the close it shows, on or before the reporting date. A close carried over a day the market was shut is current.
- **Missing:** a position with no close at all has no market value, and its account total is null with `has_missing_price` set. Flags propagate from positions to the mart, and this is tested.
- **The dev dataset:** 1 April is a reporting date, because the 31 March trades settle that day, but the simulation window ends on 31 March, so it has no closes. Its 10 instrument prices are therefore flagged stale.

**Every translation names its rate (INV-07).**
- **Recorded on each row:** USD amounts are translated at `int_fx_rates_applicable`. Each translated row records the rate, its date (on or before the business date), and the FX policy version, and a test checks them against `fct_fx_rates_daily`.
- **Cross-check:** the warehouse fallback and the brokerage's conversion pricing implement policy v1 separately. A test requires them to agree for every client conversion.

**Tie-outs (INV-10).** A test recomputes each gate mart's totals from what it is built from:
- each account's last client cash balance = its 2100 lines
- each finance column = its GL account
- execution commission and clearing fees = the trade lines on 4100 and 5100
- each account-day portfolio value = its positions

**Other tests.**
- INV-03 (`assert_position_reconciliation`): every daily position equals the cumulative executed quantity, recomputed from `fct_executions`, and is never negative.
- Each execution's gross amount from the ledger equals quantity × price × multiplier, rounded half-even.
- Every composite grain is unique.
- dbt unit tests cover:
  - staleness on market holidays
  - half-even ties (10.125 → 10.12, 10.135 → 10.14)
  - a missing price
  - USD translation and the weekend fallback
  - client cash on quiet days and a loan
  - an account with an unpriced position

**Dev dataset (seed 42).** All 174 dbt nodes pass. The dataset has:
- 63 reporting dates (2026-01-02 to 2026-04-01)
- 8,739 daily positions
- 5,426 client cash rows, with 45 account-currency loans on the last date and no CASH account ever negative
- 128 execution-mart rows covering all 784 executions
