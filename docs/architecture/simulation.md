# Brokerage simulation

| | |
|---|---|
| Status | Implemented: the engine, scripted scenarios, and the seeded generator (Phase 2C); external records and defect injection (Phase 2D). |
| Last updated | 2026-11-19 |
| Related | [Domain model](domain-model.md) §4, §9; [requirements](../business/requirements.md) FR-SIM-01, FR-SIM-05; [roadmap](../project/roadmap.md) |

All simulated data is **synthetic**. Instruments, prices, FX rates, and client behaviour are invented and labelled illustrative. No real securities or market data are used.

## Pieces

| Module | Role |
|---|---|
| `northstar.simulation.engine` | The simulated brokerage. It receives **actions** in time order (open account, deposit, withdraw, convert, place order with fills and optional cancellation, daily close) and emits canonical events. |
| `northstar.simulation.scenario` | Scripted scenarios (YAML): one account and exactly what it does. Used for golden tests: `sprint1_scenario.yaml` and `flagship_phase2.yaml` (charter §4, steps 1–5). |
| `northstar.simulation.generator` | The seeded random generator. It reads a dataset config (`config/simulation/datasets/dev.yaml`) and drives the engine day by day. |
| `northstar.simulation.market` | Synthetic closes and USD/CAD reference rates (a log-normal walk). |
| `northstar.simulation.external` | Derives the outside parties' records from the canonical events only (FR-SIM-02). |
| `northstar.simulation.defects` | Injects labelled defects into those records and writes the manifest (FR-SIM-03). |
| `northstar.application.simulate` | `northstar simulate` / `make simulate`. It writes `data/generated/<dataset>/seed=<n>/` with `canonical/events.jsonl`, `external/<set>.jsonl`, and `defects/manifest.jsonl`. |

## What the engine does for each action

- **Applies policy:** commission and clearing fee (fee schedule), the settlement date (calendar and settlement rules), the FX quote (`domain.fx`, spread policy), and margin interest (margin policy).
- **Keeps client balances by posting every event through the ledger's `PostingEngine`**, so there is one implementation of cash effects (ADR 007). Positions come from `PositionBook`.
- **Refuses** what a brokerage would refuse, with `ScenarioError`:
  - overdrawing a CASH account in any currency
  - a MARGIN loan above the simulator guard. This is CAD 50,000, a guard on the simulation, **not a margin requirement**; requirements are Phase 5.
  - withdrawing or converting money the client doesn't have
  - short sales
  - fills off a trading day
  - actions out of time order

  After a refusal the engine refuses all further use, because its state may be partly updated.
- **Schedules what follows from time passing:**
  - T+N settlement, one obligation per (order, trade date), stamped at 12:00 Toronto time on the settlement date
  - daily margin interest at 23:59:59 Toronto time on each calendar day, on each currency's debit balance
- **Numbers the event IDs only at the end,** after a stable sort by timestamp.

## The seeded generator

| Behaviour | How |
|---|---|
| Accounts | `count` accounts open during the first 10 business days with an opening deposit. `margin_share` of them are MARGIN accounts. |
| Deposits | On ledger business days, with probability `daily_probability × seasonal[month]`. |
| Withdrawals | Probability × `month_end_multiplier` on the last `month_end_days` business days of each month. At most `max_fraction` of CAD cash. |
| Orders | Poisson count per account and day, with intensity `daily_rate × (1 + volatility_sensitivity × (activity/mean − 1))`, where activity is the day's average absolute price move. |
| Order details | Buys are sized as a random fraction of buying power (cash, plus `margin_headroom_use` of the remaining guard headroom for MARGIN accounts). Sells take part of a position. Each order has 1–3 partial fills near the last close (`price_noise`), and with `cancel_probability` it is cancelled after a partial fill. A USD buy without USD cash first converts `usd_conversion_fraction` of CAD. |
| Prices and FX | A daily close for each instrument on its market's trading days (`price_updated`). A reference rate for each ledger business day; other days use the most recent prior rate (FX fallback policy v1). |

These patterns are FR-SIM-05, and they are tested on generated data:
- On the dev dataset (seed 42), 10 of 25 withdrawals fall on the 6 month-end days out of 61 business days.
- Orders per day correlate with the day's average absolute price move at 0.71.
- With 400 accounts, February deposits run at more than 1.2× March per business day (factors 1.4 vs 1.0).

### Determinism

`numpy.random.SeedSequence(seed)` is split into a market stream (child 0) and one stream per account (W4). Only the random draws come from numpy (PCG64). Everything after them, including `exp`, is `Decimal` arithmetic in an explicit context. Floating-point `exp` can differ across CPUs and math libraries, so this keeps the output **byte-identical on every machine**.

Note that the event IDs come from global sequences. Adding an account therefore shifts later IDs, even where the account's behaviour is unchanged.

### The dev dataset (seed 42)

`make simulate` produces 4,979 events. All 11 event types appear, along with partial fills, cancellations, sells, CAD and USD trades, options, clearing fees, FX conversions, withdrawals, and margin interest. The events file's SHA-256 is printed by the command, and CI regenerates and compares it (Phase 2E).

## External records (FR-SIM-02)

External records are derived **from the canonical events only**, never from ledger lines or warehouse tables, so reconciliation compares two independently produced sources (risk R-04). Schemas are in `contracts/external/`.

| Set | One per | Broker reference | Reconciliation (domain model §7.1) |
|---|---|---|---|
| `clearing_confirmations` | execution | `execution_id` | executions |
| `settlement_confirmations` | settlement (contractual and actual dates) | `settlement_id` | settlements |
| `bank_statement_lines` | deposit, withdrawal, and settlement cash flow (signed from the bank's view, on the value date) | `cash_movement_id` / `settlement_id` | cash (GL 1000) |
| `custody_snapshots` | account × instrument × ledger business day with a settled position | — | positions |
| `fee_records` | execution with a clearing fee | `execution_id` | fees (GL 5100) |
| `fx_confirmations` | conversion | `fx_conversion_id` | FX |
| `price_feed` | instrument × price date | — | stale-price checks (INV-08) |

With no defects, the bank statement equals GL 1000 per currency, and the last custody snapshot equals the settled positions. Both are tested.

## Defects and the manifest (FR-SIM-03)

`config/simulation/defects/dev.yaml` sets a per-record rate for each defect (illustrative). Defects are drawn from their own stream, `SeedSequence([seed, 2])`, so changing defect rates never changes the canonical events. A record gets at most one defect.

| Defect | Change | Expected exception (§7.2) | Impact |
|---|---|---|---|
| `DROPPED_RECORD`, clearing confirmation | removed | `MISSING_EXTERNAL` | gross amount of the execution |
| `DROPPED_RECORD`, settlement confirmation | removed | `MISSING_EXTERNAL` | settlement amount |
| `DROPPED_RECORD`, bank statement line | removed | `CASH_BREAK` | absolute amount |
| `DUPLICATE_CLEARING_MESSAGE` | sent twice (new record ID, same broker reference) | `DUPLICATE_EXTERNAL` | gross amount |
| `WRONG_FEE` | fee × `wrong_fee_factor` | `FEE_MISMATCH` | fee difference |
| `DELAYED_SETTLEMENT` | actual date and bank value date + 1–3 ledger business days | `SETTLEMENT_LATE` | settlement amount |
| `WRONG_FX_CONVERSION` | buy amount × (1 − shift) | `FX_MISMATCH` | buy-amount difference |
| `STALE_PRICE` | previous close repeated | `STALE_PRICE` (INV-08) | price difference **per unit** |

Each manifest line (`contracts/external/defect_manifest.v1.schema.json`) records:
- the defect ID, type, and record set
- the affected record IDs and the broker reference
- the expected exception
- the impact (amount, currency, basis)
- the defect-config version

Tests prove the manifest is **exact**. Every changed, dropped, or added record appears in exactly one entry, every entry corresponds to a change, and each impact is recomputed independently.

The dev dataset with seed 42 has 71 defects: 17 dropped, 8 duplicates, 22 wrong fees, 12 delayed settlements, 1 wrong FX conversion, and 11 stale prices.

## Loading a dataset and the Phase 2 gate

`make simulate-load` loads the dev dataset into its own schemas (`raw_dev`, `ledger_dev`), so its IDs never collide with the Sprint 1 slice's, which uses the same seed. It then builds dbt over them (`--vars raw_schema/ledger_schema`, target schema `dbt_dataset`) and prints KPI-01. The gate's checks are listed in the [roadmap](../project/roadmap.md#phase-2-gate-evidence).

## Simplifications (†)

- Interest accrues on the trade-date 2100 balance, not the settled balance.
- Clearing-fee invoices are not paid (2200 accumulates the fees).
- FX covering trades are not simulated (2300 holds the open position).
- The generator converts CAD → USD only. The engine and posting rules support both directions, which the scripted and property tests exercise.
- Options expire after the window. Exercise, assignment, and expiry are Phase 5.
