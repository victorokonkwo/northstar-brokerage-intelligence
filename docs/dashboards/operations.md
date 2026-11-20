# Operations dashboard (Looker Studio)

| | |
|---|---|
| Status | Spec ready; the report is built by the owner in Looker Studio |
| Requirement | FR-M2-09 (KPI-02 to KPI-06), FR-DASH-01 (version and as-of time on every page) |
| Decision | [ADR 011](../adr/011-looker-studio-dashboards.md) |
| Data | the serving views in `dbt_dataset_serving` on Redshift, read as the `looker` user |

The report shows reconciliation health for brokerage operations: what matched, what's open, how much is exposed, what's ageing past its SLA, what failed to settle, and what's breaking in cash. **It calculates nothing.** Every figure is a value from one row of a serving view, and the views equal the published snapshots and the marts (`make dashboard-check`).

All data is synthetic, and the thresholds are illustrative (README, disclaimer).

## Connection (once)

1. **Prerequisites:**
   - `make env`, to add `NBI_LOOKER_PASSWORD` to `.env` if it's missing
   - a serving build on Redshift
   - then, as the admin: `northstar --warehouse redshift redshift-bootstrap --raw-schema raw_dev --ledger-schema ledger_dev --recon-schema recon_dev --dbt-schema dbt_dataset --looker-user`
2. **Connector:** in Looker Studio, Create, then Data source, then **Amazon Redshift**. Choose **BASIC**:

   | Field | Value |
   |---|---|
   | Hostname | `terraform -chdir=infrastructure/terraform/data output -json redshift_endpoint`, its `host` |
   | Port | `5439` |
   | Database | `northstar` |
   | Username | `looker` |
   | Password | `NBI_LOOKER_PASSWORD` from `.env` |

   Tick **Enable SSL**, because Redshift requires TLS. If Looker Studio asks for the server's CA certificate, use the bundle AWS publishes for Redshift. That step hasn't been tried yet, so record what it needed.
3. **Data sources:** create four, one per table of the `dbt_dataset_serving` schema: `serving_match_rates`, `serving_exception_monitoring`, `serving_settlement_fails`, `serving_cash_breaks`. Keep every field's type as Looker Studio detects it, but make `business_date` a **Date**.

## Rules for every chart

- **Show rows, not sums.** A chart shows values at the view's grain, or picks exactly one row per value it shows.
  - Aggregation "Sum" or "Max" over a single row displays that row's value, which is allowed.
  - Totals across rows the view doesn't hold (all currencies, all reconciliations) are **not shown**: they would be calculations (W5).
- **Version and as-of time on every page** (FR-DASH-01). The header shows, for the selected business date, `snapshot_version` and `published_at`, as scorecards with aggregation Max over one date. Every row of a date carries the same pair.
- **One date at a time.** Each page has a **date control** on `business_date`, defaulting to the latest date. Time-series charts use their own date range instead.

## Pages

### 1. Match rates (KPI-02), `serving_match_rates`
Grain: business date × currency.

| Element | Fields | Notes |
|---|---|---|
| Time series | x: `business_date`; y: `match_rate`; breakdown: `currency` | one point per date and currency |
| Time series | x: `business_date`; y: `notional_match_rate`; breakdown: `currency` | |
| Table, for the selected date | `currency`, `execution_count`, `matched_count`, `match_rate`, `notional`, `matched_notional`, `notional_match_rate` | |

### 2. Exceptions (KPI-03, KPI-04), `serving_exception_monitoring`
Grain: business date × reconciliation × exception type × severity.

| Element | Fields | Notes |
|---|---|---|
| Table, for the selected date | `reconciliation`, `exception_type`, `severity`, `exception_count`, `open_count`, `past_sla_open_count`, `resolved_count`, `open_exposure_cad`, `policy_version`, `reconciled_as_of` | sort by `severity`, then `open_exposure_cad` descending; colour `past_sla_open_count` > 0 |
| Filter controls | `reconciliation`, `severity` | they filter rows, never sum them |

### 3. Settlement fails (KPI-05), `serving_settlement_fails`
Grain: contractual settlement date × currency × asset class.

| Element | Fields | Notes |
|---|---|---|
| Time series | x: `business_date`; y: `fail_rate`; breakdown: `currency` and `asset_class` (one series each) | |
| Table, for the selected date | `currency`, `asset_class`, `obligations_due`, `failed_count`, `fail_rate`, `value_due`, `value_failed`, `value_fail_rate` | |

### 4. Cash breaks (KPI-06), `serving_cash_breaks`
Grain: reporting date × currency.

| Element | Fields | Notes |
|---|---|---|
| Time series | x: `business_date`; y: `unexplained`; breakdown: `currency` | |
| Table, for the selected date | `currency`, `gl_cash_balance`, `bank_balance`, `difference`, `explained_timing`, `unexplained`, `cash_break_amount` | |

## Checking the built report

1. **The data check:**
   - Run `make dashboard-check-redshift`, which checks the data the report reads (`make dashboard-check` checks the local copy).
   - **It confirms:** every serving view shows only the currently published version of each date, equal row for row to that snapshot and to the mart.
2. **Spot check, three figures:** pick three figures shown in the report, for example a match rate, an open exposure, and an unexplained cash break. For each, query its serving view with the same filters (example below), and record the date, the filters, the report's figure, and the query's. They must be equal.

   ```sql
   select match_rate from dbt_dataset_serving.serving_match_rates
   where business_date = '2026-03-02' and currency = 'CAD';
   ```
3. **The version:** check that every page header shows the version and publication time of its date.
