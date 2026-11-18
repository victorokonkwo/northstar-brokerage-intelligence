# 007. Single owner for each calculation

| | |
|---|---|
| Status | Accepted |
| Date | 2026-11-18 |
| Deciders | Project owner |

## Context

The platform uses both SQL (dbt) and Python. If a metric is implemented twice, for example margin excess in both a dbt model and a Python service, or a KPI recomputed in a dashboard, the two will eventually disagree, and nobody will know which one is right.

## Decision

1. Every calculation has **exactly one owner**: a dbt model or a Python function. The owner is recorded in the [KPI dictionary](../business/kpi-dictionary.md) ("Calculation owner") or the model's docs.
2. **Default allocation:**
   - **dbt** owns set-based transformations, joins, aggregations, ledger-derived balances, finance marts, and control aggregations.
   - **Python** owns stateful or iterative logic: posting rules, reconciliation matching, margin and policy evaluation, VaR/ES, stress testing, option pricing, and forecasting.
3. Python results are written to the warehouse with version metadata, and dbt publishes them through marts. dbt does not recompute them.
4. Dashboards and APIs perform **no** calculations beyond formatting and filtering published data.
5. Tests may compute an expected value independently. That is verification, not a second implementation.

## Consequences

- One place to fix each definition.
- Some logic that could be written in SQL lives in Python, or the other way round. The split is decided per calculation and recorded.

## Revisit when

A calculation's owner turns out to be the wrong tool, for example a Python step that is too slow for set-based work. Change the owner by updating the KPI dictionary, never by adding a second implementation.
