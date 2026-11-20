# Incremental models and late data

| | |
|---|---|
| Status | Phase 4F-b: built on both warehouses |
| Decision | Owner, 2026-11-20: incremental dbt and incremental SCD2; the design below approved the same day, in place of the `_ingested_at` watermark first planned |
| Code | [`warehouse/dbt/macros/incremental.sql`](../../warehouse/dbt/macros/incremental.sql), [`int_arrivals`](../../warehouse/dbt/models/intermediate/int_arrivals.sql) |
| Facts | [sources.md](../sources.md): the dbt and PostgreSQL rows dated 2026-11-20 |

The dbt models used to be rebuilt in full on every run. Seventeen of them are now incremental: a build rewrites only the rows that new records can change. That includes late records, dated before data that is already built. The result must always equal a full rebuild of the same data. CI and a live Redshift run check this, row for row.

## How a build finds what changed

| Piece | What it does |
|---|---|
| Build log (`dbt_build_log`) | an `on-run-start` hook gives every `dbt run` or `dbt build` the next build number |
| `int_arrivals` | records each raw record once, in the build that first saw it, with the first business date it can change and the keys it touches: account, order, settlement, instrument. Records are found by key, not by time |
| Model builds (`dbt_model_builds`) | each incremental model records its successful build in a post-hook, inside the same transaction as its data |
| `rebuild_filter` | the records that arrived after a model's last successful build decide what it rebuilds |

**Why not a timestamp watermark?** A row's `_ingested_at` comes from `now()`, which is when its load's transaction *started* (verified on PostgreSQL 18.7). A load that starts before a dbt build but commits after it gets an older timestamp, so the next build never picks it up. Redshift stores whole seconds, so two loads can share a timestamp. Build numbers order builds without a clock. A build that fails records nothing, so the next build repeats its work.

**One build at a time.** Builds of the same schema must not overlap. The Airflow `warehouse` pool already serialises them, two concurrent builds deadlock in Postgres anyway, and the cloud run (4G) runs one execution at a time.

## What each model rebuilds

| Rule | Models | What is rewritten |
|---|---|---|
| By key: new records | `fct_executions`, `fct_journal_lines`, `fct_cash_movements`, `fct_fx_rates_daily`, `dim_calendar` | the rows of the new records. Their rows never change afterwards |
| By key: affected entities | `fct_orders`, `fct_settlements`, `dim_account`, `dim_instrument` | every row of each order, settlement, account, or instrument with a new record. For SCD2 (`dim_account`), the account's whole version chain is recomputed. So a late update closes the version before it (`valid_to`, `is_current`) and is closed by the one after |
| By date | `fct_market_prices_daily`, `fct_positions_daily`, `mart_daily_client_cash`, `mart_daily_positions`, `mart_daily_executions`, `mart_daily_finance`, `mart_ledger_balance_controls` | every row from the earliest date a new record can change, onward. Balances and positions carry forward, so a late record changes every later date |

- **The `rebuild` strategy.** It deletes every row in the rebuilt range or keys, then inserts the recomputed rows, in one transaction. dbt's built-in `delete+insert` deletes only the rows whose key comes back. A position that a late sale closes produces no row, so its stale rows would survive. Replacing the strategy with `delete+insert` makes the test below fail on exactly those rows.
- **Effective dates** (`int_arrivals`):
  - a trade counts from its trade date, and a settlement from its settlement date
  - a price from its price date, and an FX rate from its rate date
  - a journal line from the earlier of its business and posting dates, because it can also extend the reporting dates
  - a calendar day from its date
  - an instrument listing from the beginning, because instruments join every reporting date
- **The inputs list.** Each model lists the staging models whose records it rebuilds on (`meta.rebuild.inputs`). `tests/unit/test_dbt_incremental.py` parses the project and fails CI unless that list equals the staging models the model actually reads, through any intermediate model. A missing input would mean silently stale numbers.
- **Over-approximation.** Rebuilding more than needed is allowed, and missing anything is not. For example, any new journal line rebuilds positions from its date, because journal lines set the reporting dates.

## When a build rebuilds everything

- **Automatically**, when a model has no recorded build (its first build, or a table built before this existed), or when its own SQL file changed since its last build (the file checksum dbt records).
- **By hand, with `dbt build --full-refresh`**, after a change the checksum can't see:
  - a staging view or intermediate model it reads
  - a macro
  - `dbt_project.yml`
  - a column type in a contract
- **Column changes.** `on_schema_change: fail` refuses an added or removed column, so a contract change also needs `--full-refresh`.
- **CI** always builds from an empty database, so none of this affects it.

## Proof

| Check | What it shows |
|---|---|
| `tests/integration/test_incremental_models.py`, scenario in `tests/incremental_scenario.py` (Postgres, in CI) | **Late rounds.** The seed-42 dev dataset is loaded with some of its own events held back. They arrive in five late rounds, each dated at least a week after the one before: a sale that closes a position for good, a deposit, a price, an FX rate, and an account update.<br>**Each round** must rewrite exactly the rows from its date onward in the daily models it can affect, and nothing in the others. A price must not touch client cash.<br>**The sale** must remove the closed position's rows.<br>**At the end**, every model must equal a `--full-refresh` build, and a rerun with no new data must write nothing |
| `tests/redshift/test_redshift_incremental.py` (live, `make test-redshift`) | the same scenario on Redshift |
| CI step "Incremental builds equal a full refresh" | the pipeline's own `dbt_dataset`, built incrementally from the first load through the late deposit, equals `make dbt-build-full`, model for model (`make compare-incremental`) |
| `tests/unit/test_dbt_incremental.py` (CI) | every incremental model uses the `rebuild` strategy, `on_schema_change: fail`, and the build-recording post-hook, and rebuilds on exactly the staging models it reads |

## Commands

| Target | What it does |
|---|---|
| `make dbt-build-dataset` | incremental build of `dbt_dataset` (the first build is a full one) |
| `make dbt-build-full` | full-refresh build of the same data into `dbt_dataset_full` |
| `make compare-incremental` | every model in `dbt_dataset` against `dbt_dataset_full`, row for row |

## Not claimed

- **Speed.** No measurement of the time saved is recorded yet; that belongs with the Phase 8 load test.
- **Compute.** The date-ruled models filter their final rows, so some recompute history before filtering. Client cash, for example, recomputes running balances from the first date. What is incremental for them is the rows written, not all the computation.
