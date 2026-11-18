# 004. Fixed-precision decimal money

| | |
|---|---|
| Status | Accepted |
| Date | 2026-11-18 |
| Deciders | Project owner |

## Context

Binary floating point cannot represent most decimal fractions exactly. For example, `0.1 + 0.2 != 0.3` in IEEE 754 doubles. Accounting figures must balance exactly, and small representation errors accumulate across millions of postings.

## Decision

### Types

| Kind | Python | SQL | Scale |
|---|---|---|---|
| Posted monetary amount (CAD, USD) | `Decimal`, quantized to the currency's minor unit | `numeric(18, 2)` | 2 |
| Price (per unit) | `Decimal` | `numeric(18, 8)` | 8 |
| Quantity (supports fractional shares) | `Decimal` | `numeric(18, 8)` | 8 |
| FX rate | `Decimal` | `numeric(18, 10)` | 10 |
| Aggregates (sums of amounts) | `Decimal` | the type the engine widens to. Redshift `SUM` of `DECIMAL(p, s)` returns `DECIMAL(38, s)`. | as input |
| Intermediate calculations (interest accrual, allocation) | `Decimal` at the project context precision | `numeric` at the widest needed precision | n/a, quantized at posting |

Why precision 18 and not Redshift's maximum of 38: according to the AWS Redshift docs, DECIMAL values with ≤ 19 significant digits are stored as 8-byte integers, and those with 20–38 digits as 16-byte integers. The docs also warn that `MEDIAN`/`PERCENTILE_CONT` may be inaccurate or fail at maximum precision. Precision 18 with scale 2 holds amounts up to ±10¹⁶, far beyond this platform's needs ([sources](../sources.md)).

### Rules

1. A `Money` value object (`northstar.common.money`, Phase 2) holds `(amount: Decimal, currency: Currency)`. It is immutable. Constructing it from a `float` raises `TypeError`. Adding amounts in different currencies raises an error.
2. The Python decimal context (precision and rounding) is set explicitly in one place, `northstar.common.money`. Code must not rely on interpreter defaults. The default context is 28 significant digits with `ROUND_HALF_EVEN`, verified on the dev machine on 2026-11-18. Setting it explicitly protects against code elsewhere changing the thread's context.
3. **Rounding mode is `ROUND_HALF_EVEN`.** Rounding happens only when an amount is posted to the ledger or written to a reporting output. Nothing is rounded mid-calculation.
4. When an amount is split (e.g., cost allocation), the parts are rounded with the largest-remainder method so they sum exactly to the original.
5. JSON carries decimals as strings. Parquet uses decimal logical types.
6. pandas/NumPy `float64` may be used for **risk and forecasting analytics** (returns, VaR scenarios, model features), where floating point is standard. Results that flow back into financial reports are converted to `Decimal`/`numeric` at the boundary and labeled as model outputs, not ledger amounts.

## Consequences

- Exact balancing of journals.
- `Decimal` arithmetic is slower than float. Bulk aggregation belongs in SQL, where `numeric` is efficient enough at this scale. This will be verified in the Phase 3 benchmark.
- The float boundary in rule 6 must be explicit in code and reviewed.

## Revisit when

A currency with a different minor unit is added, or benchmarks show `Decimal` is a bottleneck in a hot path.
