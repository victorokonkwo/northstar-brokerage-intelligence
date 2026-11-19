# KPI dictionary

| | |
|---|---|
| Status | Approved 2026-11-18. **No KPI is implemented yet.** |
| Last updated | 2026-11-18 |

These are the first 20 KPIs. Each entry states the decision it supports, its exact definition, its grain, its authoritative source, the single owner of the calculation ([ADR 007](../adr/007-single-owner-for-each-calculation.md)), and how its correctness is verified.

## Conventions that apply to every KPI

- **Reporting currency** is CAD. Amounts in USD are translated at the Bank of Canada daily rate for the business date ([domain model §6](../architecture/domain-model.md)). The original-currency amount is always available alongside the translated one.
- **Business date** follows domain model §5. "End of day" means the state after all events for that business date have been processed in the day's batch run.
- **Versioning.** Every persisted KPI value carries `snapshot_version`, `run_id`, and any relevant `policy_version` or `model_version`. A published value is never overwritten; corrections create a new snapshot version ([ADR 006](../adr/006-bitemporal-records-and-restatements.md)).
- **Status.** Every KPI is *planned*. Its "Verification" line names the test that must exist before the KPI can be marked implemented.
- **Model names** follow the target design in the [domain model](../architecture/domain-model.md) and may be refined during implementation. Update this file when they change.

**Persona owners:** OPS = brokerage operations, RISK = market risk, TRS = treasury, FIN = finance lead, AE = analytics engineering.

---

## Financial controls (M1/M2)

### KPI-01 Unbalanced journal batches
- **Decision supported:** whether the day's ledger can be used for any downstream reporting. This is a hard gate.
- **Definition:** the count of `(journal_batch_id, currency)` groups among accepted batches where Σ `debit_amount` − Σ `credit_amount` ≠ 0.
- **Target:** exactly 0. Any non-zero value blocks publication.
- **Grain:** business date. **Unit:** count.
- **Owner:** AE. **Calculation owner:** dbt (singular test plus control mart).
- **Source:** `fct_journal_lines`.
- **Verification:** the dbt test `assert_balanced_journals` (planned, Sprint 1 Day 9), plus the Python property test `tests/financial_invariants/test_double_entry.py`, which shows every posting rule produces balanced batches for generated inputs (implemented).

### KPI-02 Execution match rate
- **Decision supported:** whether internal trade records can be trusted for the day, and how much exception workload to expect.
- **Definition:** internal executions matched to a clearing confirmation (by exact, composite, or tolerance rule) ÷ all internal executions with that trade date. Also reported notional-weighted: Σ matched `abs(quantity × price)` ÷ Σ all.
- **Grain:** trade date × currency. **Unit:** %.
- **Owner:** OPS. **Calculation owner:** Python reconciliation service produces match results; dbt aggregates them in `mart_exception_monitoring`.
- **Source:** reconciliation match results, `fct_executions`.
- **Verification:** on a seeded dataset with a known injected-defect manifest, the computed rate equals the rate derived from the manifest.

### KPI-03 Open exceptions and monetary exposure
- **Decision supported:** where operations should focus, and whether publication is blocked.
- **Definition:** the count of exceptions with status not in (`RESOLVED`) at end of day, and Σ `abs(financial_impact_cad)`, by exception type and severity.
- **Grain:** business date × exception type × severity. **Unit:** count, CAD.
- **Owner:** OPS. **Calculation owner:** dbt.
- **Source:** `fct_reconciliation_exceptions`.
- **Verification:** fixture-based test with a known exception set and expected counts and sums.

### KPI-04 Exceptions past SLA
- **Decision supported:** escalation.
- **Definition:** the count of open exceptions whose age in business days exceeds the SLA configured for their severity. The SLA values are in config, not code.
- **Grain:** business date × severity. **Unit:** count.
- **Owner:** OPS. **Calculation owner:** dbt.
- **Source:** `fct_reconciliation_exceptions`, `dim_calendar`.
- **Verification:** a test across a holiday boundary confirms that age is counted in business days.

### KPI-05 Settlement fail rate
- **Decision supported:** counterparty and operational follow-up, and treasury funding for failed deliveries.
- **Definition:** obligations with contractual `settlement_date = d` that were not settled by end of day `d` ÷ all obligations due on `d`. Reported by count and by value.
- **Grain:** settlement date × currency × asset class. **Unit:** %.
- **Owner:** OPS. **Calculation owner:** dbt.
- **Source:** `fct_settlements`.
- **Verification:** injected late-settlement scenarios produce the expected numerator.

### KPI-06 Cash break amount
- **Decision supported:** whether ledger cash agrees with the bank, which is a precondition for treasury reporting.
- **Definition:** for each bank account and currency, `abs(GL cash account closing balance − external bank statement closing balance)`.
- **Target:** 0 after explained timing differences. Any unexplained difference is an exception.
- **Grain:** business date × bank account × currency. **Unit:** original currency, plus CAD.
- **Owner:** OPS, FIN. **Calculation owner:** Python reconciliation service (statement matching); dbt for the balance roll-forward.
- **Source:** `fct_journal_lines`, external bank statement records.
- **Verification:** a cash-conservation invariant test, plus injected cash-break scenarios.

## Market risk (M3)

### KPI-07 Accounts in margin deficit
- **Decision supported:** margin calls and possible liquidation review.
- **Definition:** the count of margin accounts with `margin_excess < 0` at end-of-day valuation, and Σ `abs(margin_excess)` over those accounts. `margin_excess = account_equity − margin_requirement` ([glossary](business-glossary.md)).
- **Grain:** business date × policy version. **Unit:** count, CAD.
- **Owner:** RISK. **Calculation owner:** Python `domain/market_risk/margin` writes `fct_margin_exposure_daily`; dbt aggregates.
- **Source:** `fct_positions_daily`, `fct_market_prices_daily`, `dim_instrument`, client cash balances from `fct_journal_lines` (account 2100 sub-ledger), and the margin policy config (`config/risk/margin_policies.yaml`).
- **Verification:** hand-calculated reference accounts, including the boundary case where excess is exactly 0.

### KPI-08 Margin loan balance
- **Decision supported:** credit exposure and funding needs.
- **Definition:** Σ client debit cash balances (as positive amounts), translated to CAD, at end of day.
- **Grain:** business date × currency. **Unit:** CAD.
- **Owner:** RISK, TRS. **Calculation owner:** dbt, derived from the ledger.
- **Source:** `fct_journal_lines` (client cash sub-ledger).
- **Verification:** equals the sum of negative client balances reconstructed independently from the journal in a Python invariant test.

### KPI-09 Account 1-day 99% historical VaR
- **Decision supported:** risk-based monitoring of margin accounts and review of margin policy adequacy.
- **Definition:** the 99th percentile loss of the account's current portfolio, revalued under each historical 1-day scenario in the lookback window. Reported as a positive amount. Options are fully revalued rather than delta-approximated.
- **Deferred parameters (Phase 5, OQ-08):** the lookback window length and the quantile interpolation method are **not yet fixed**. They will be set in the VaR methodology doc before KPI-09 is implemented, and every result will record them. Apart from those two parameters, the definition is complete.
- **Grain:** account × business date × model version. **Unit:** CAD.
- **Owner:** RISK. **Calculation owner:** Python `domain/market_risk/value_at_risk`.
- **Source:** `fct_positions_daily`, historical `fct_market_prices_daily`, `dim_instrument` (option terms for revaluation).
- **Output:** `fct_portfolio_risk_daily`.
- **Verification:** reference cases with hand-computable quantiles. The result is invariant to the order of scenarios.

### KPI-10 Account 1-day 97.5% Expected Shortfall
- **Decision supported:** tail-risk monitoring beyond VaR.
- **Definition:** the mean of scenario losses at or beyond the 97.5% quantile, using the same scenarios as KPI-09. It inherits KPI-09's deferred parameters (OQ-08).
- **Grain:** account × business date × model version. **Unit:** CAD.
- **Owner:** RISK. **Calculation owner:** Python `domain/market_risk/expected_shortfall`.
- **Source:** the same scenario set as KPI-09 (`fct_positions_daily`, historical `fct_market_prices_daily`, `dim_instrument`). **Output:** `fct_portfolio_risk_daily`.
- **Verification:** reference cases. ES ≥ VaR at the same confidence level for every portfolio, checked as a property test.

### KPI-11 Stressed margin deficit
- **Decision supported:** how many accounts would need margin calls under a defined market shock.
- **Definition:** for each versioned stress scenario (e.g., `EQ_DOWN_20`), the count and Σ deficit of accounts whose stressed equity falls below the stressed margin requirement.
- **Grain:** business date × scenario × policy version. **Unit:** count, CAD.
- **Owner:** RISK. **Calculation owner:** Python `domain/market_risk/stress_testing`.
- **Source:** `fct_positions_daily`, `fct_market_prices_daily`, `dim_instrument`, client cash balances from `fct_journal_lines`, the margin policy config, and the stress scenario config (`config/risk/stress_scenarios.yaml`).
- **Verification:** a zero-shock scenario reproduces KPI-07 exactly. A reference portfolio produces hand-calculated results.

### KPI-12 Stressed uncollateralized exposure
- **Decision supported:** the broker's potential credit loss if collateral falls in value.
- **Definition:** Σ over accounts of `max(0, −stressed_account_equity)`, i.e., loan balances that stressed collateral no longer covers.
- **Grain:** business date × scenario. **Unit:** CAD.
- **Owner:** RISK. **Calculation owner:** Python `domain/market_risk/stress_testing`.
- **Source:** `fct_positions_daily`, `fct_market_prices_daily`, `dim_instrument`, client cash balances from `fct_journal_lines`, and the stress scenario config. No margin policy is needed, because this measures equity, not requirement.
- **Verification:** reference cases where the exposure is 0, partially covered, and fully uncovered.

### KPI-13 Single-issuer concentration
- **Decision supported:** concentration limits and haircut adjustments.
- **Definition:** for an account, max over issuers of (issuer long market value ÷ total long market value). Firm-wide, an issuer's total market value across margin accounts ÷ total margin collateral market value. Option exposure is measured by delta-adjusted underlying value.
- **Grain:** account (or firm) × business date. **Unit:** %.
- **Owner:** RISK. **Calculation owner:** Python `domain/market_risk/exposures`.
- **Source:** `fct_positions_daily`, `fct_market_prices_daily`, `dim_instrument` (issuer; option underlying), and option deltas from the M3 Greeks calculation (`domain/market_risk/greeks`, which owns delta).
- **Verification:** an account with a single issuer gives 100%. Accounts with n equal-weighted issuers give 1/n.

### KPI-14 VaR backtest exceptions
- **Decision supported:** whether the VaR model can still be relied on.
- **Definition:** over the trailing 250 business days, the count of days where the hypothetical P&L loss (the start-of-day portfolio revalued at end-of-day prices) exceeded the prior day's 99% VaR. Reported with the Kupiec proportion-of-failures p-value and the Christoffersen independence p-value.
- **Grain:** account cohort (or firm) × business date × model version. **Unit:** count, p-values.
- **Owner:** RISK. **Calculation owner:** Python `monitoring/model_monitoring/risk_backtesting`.
- **Source:** prior-day VaR from `fct_portfolio_risk_daily`; start-of-day portfolio from `fct_positions_daily`; end-of-day prices from `fct_market_prices_daily`.
- **Verification:** simulated P&L drawn from the model's own distribution produces exception rates consistent with 1%, and the test statistics match reference implementations on fixed inputs.

## Treasury (M4)

### KPI-15 Net cash-flow forecast
- **Decision supported:** daily funding and liquidity positioning.
- **Definition:** for each currency and horizon h ∈ {1, 7, 30} business days, the forecast net cash flow is the sum of (a) **known obligations** (unsettled trades due within h, read from `fct_settlements`, deterministic) and (b) **forecast uncertain flows** (client deposits and withdrawals). Part (b) is reported as P10, P50, and P90.
- **Grain:** forecast run × currency × horizon × scenario. **Unit:** CAD / USD.
- **Owner:** TRS. **Calculation owner:** Python `ml/forecasting` (b) plus dbt (a), combined in `mart_liquidity_forecast`.
- **Source:** (a) `fct_settlements` (open obligations); (b) training history of client deposits and withdrawals from `fct_cash_movements`. The rest of the feature set is defined in Phase 6 and recorded in the model card. **Output:** `fct_liquidity_forecasts`.
- **Verification:** the known-obligations component equals the sum of open obligations in fixtures. The forecasting pipeline is reproducible for a fixed training cutoff and seed.

### KPI-16 Liquidity headroom
- **Decision supported:** whether liquid resources cover projected outflows under stress.
- **Definition:** `available_liquidity_at_origin − cumulative projected net outflow over horizon h` under a named scenario. The base scenario uses the P90 outflow.
- **Deferred parameter (Phase 6, OQ-06):** the components and source of `available_liquidity` are **not yet defined**. This was deferred by owner decision on 2026-11-18. The parameter must be defined in the liquidity methodology doc before KPI-16 is implemented, and every result will record the definition version. Apart from this parameter, the definition is complete.
- **Grain:** forecast run × currency × horizon × scenario. **Unit:** CAD / USD.
- **Owner:** TRS. **Calculation owner:** Python `domain/treasury/liquidity`.
- **Source:** projected outflows from `fct_liquidity_forecasts`. The source of `available_liquidity` is deferred with OQ-06.
- **Verification:** reference scenarios with hand-computed results.

### KPI-17 Forecast accuracy and calibration
- **Decision supported:** how much to trust the forecast, and whether it beats the simple baseline.
- **Definition:** in walk-forward evaluation, **WAPE** = Σ\|actual − P50\| ÷ Σ\|actual\| per horizon. **Coverage** = the share of actuals inside [P10, P90], compared with the nominal 80%. Always reported next to the seasonal-naive baseline. A model that does not beat the baseline is reported as such.
- **Grain:** model version × horizon × evaluation window. **Unit:** %.
- **Owner:** TRS. **Calculation owner:** Python `ml/forecasting/evaluation`.
- **Source:** walk-forward forecasts produced by the evaluation pipeline (same model code as `fct_liquidity_forecasts`), and realized client flows from `fct_cash_movements` as actuals.
- **Verification:** metric functions tested on hand-computed fixtures. Walk-forward splits are tested so that no training row is dated on or after its forecast origin.

## Finance (M5)

### KPI-18 Product contribution margin
- **Decision supported:** which products are sustainably profitable.
- **Definition:** for each product (equities and ETFs trading, options trading, margin lending, FX conversion), revenue minus direct costs, both taken from ledger revenue and expense accounts mapped to products in a versioned mapping. Contribution margin % = contribution margin ÷ revenue. Allocated indirect costs are reported separately and never mixed into contribution margin.
- **Grain:** product × month × reporting currency × snapshot version. **Unit:** CAD, %.
- **Owner:** FIN. **Calculation owner:** dbt `mart_product_profitability`.
- **Source:** `fct_journal_lines`, the product account mapping.
- **Verification:** Σ product revenue equals the total of the ledger revenue accounts for the period (a reconciliation tie-out test). Every mart row traces back to journal lines.

### KPI-19 Revenue per active account
- **Decision supported:** unit economics and cohort comparison.
- **Definition:** total revenue for the month ÷ the count of active accounts at month end. An active account has at least one client-initiated execution or cash movement in the trailing 90 calendar days.
- **Grain:** month × (optionally) signup cohort. **Unit:** CAD.
- **Owner:** FIN. **Calculation owner:** dbt.
- **Source:** revenue from `fct_journal_lines` (revenue accounts 4100–4300); activity from `fct_executions` and `fct_cash_movements`; signup cohort from `dim_account` (open date).
- **Verification:** fixture with known activity dates, including the 90-day boundary.

### KPI-20 Margin lending net interest margin
- **Decision supported:** whether margin lending is priced adequately for its funding cost.
- **Definition:** (margin interest income − funding cost attributed to margin loans) ÷ average daily margin loan balance, annualized by × 365 ÷ the number of days in the period. Funding cost uses a simulated funding rate from versioned config.
- **Grain:** month × currency. **Unit:** % annualized.
- **Owner:** FIN, TRS. **Calculation owner:** dbt.
- **Source:** `fct_journal_lines`, KPI-08 daily balances.
- **Verification:** a reference month with constant balance and rate reproduces the configured spread.

---

## Backlog: KPIs not yet defined

These are cost per trade, client cohort retention, forecast vs. actual variance for finance, and option Greeks aggregates. They will be added with full definitions before they are implemented.
