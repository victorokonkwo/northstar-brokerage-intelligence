# Reconciliation

| | |
|---|---|
| Status | Partly implemented (Phase 4): the policy (4A); staged matching and classification (4B). The lifecycle, publication gate, marts, and dashboard follow in 4C–4G ([roadmap](../project/roadmap.md#phase-4-weeks-89)). |
| Requirements | FR-M2-02 to FR-M2-04, FR-M2-07, NFR-03 ([requirements](../business/requirements.md)) |
| Definitions | domain model §7 ([domain model](domain-model.md#7-reconciliation-exceptions)) |

## Inputs

Each reconciliation compares two independently produced sources (FR-SIM-02). The internal side comes from the warehouse's core models and journal; the external side comes from `stg_external__*`. A run reads only what is dated on or before its as-of date: a settlement confirmation that arrives later is not yet known.

| Reconciliation | Internal | External | Stages (each match records its rule and version) |
|---|---|---|---|
| executions | `fct_executions`, gross from its journal lines | clearing confirmations | `exact_id` (execution ID) → `composite` (account, instrument, side, trade date, quantity, price) → `tolerance` (price ± 0.0001) |
| settlements | `fct_settlements` (SETTLED) | settlement confirmations | `exact_id` → `composite` (account, instrument, settlement date, quantity) |
| cash | GL 1000 lines, by the reference the bank echoes (cash movement or settlement ID) | bank statement lines | `exact_reference` → `composite` (value date, amount, currency); `late_settlement_timing` for a late settlement's bank line |
| positions | `fct_positions_daily` settled quantity | custody snapshots | `composite` (account, instrument, date); custody lists only positive holdings, so a missing line is zero |
| fees | clearing fees from the journal (`int_execution_amounts`) | fee records | `exact_id` → `composite` (account, trade date, amount) |
| FX | client conversions (`fct_cash_movements`) | FX confirmations | `exact_id` |
| prices | daily closes | vendor price feed | `composite` (instrument, date); a vendor close equal to our previous close is `STALE_PRICE` |

## Classification

A break is the raw finding: type, reference, business date, and impact in the record's own currency (or per unit, or in units for positions). `classify` then applies the versioned policy (`config/reconciliation/policy.yaml`):
- **CAD impact:** USD impacts are translated at the rate in force on the business date and rounded half-even once (ADR 004). A date with no rate is an error, not a guess.
- **Severity:** set by the CAD thresholds, with fixed floors for some types.
- **Exception ID:** a UUIDv5 of the reconciliation, type, reference, and external records. A rerun with the same inputs therefore stores nothing new (FR-M2-07).

Python owns matching, impact, and severity (ADR 007); dbt aggregates them (4D).

**One late settlement, one exception.** Settlements run before cash. A confirmation settled after its contractual date is `SETTLEMENT_LATE`. Its bank line, which arrives on the actual date, is matched as an explained timing difference, and its record ID is added to the same exception. A value-date shift with no late settlement behind it is a `CASH_BREAK`.

## Ground truth (NFR-03)

The simulator's defect manifest is the answer key. Two checks hold:
- **Unit test:** both sides are built from the generated events and records. Exact external records reconcile with zero breaks over more than 10,000 matches. With the injected defects, the exceptions equal the manifest: the same type, reference, impact, and external records wherever those records still exist, with nothing missing and nothing extra.
- **Pipeline (`make reconcile`, in CI on every push):** reconcile the loaded dev dataset, then `northstar recon-verify` compares the stored exceptions with `defects/manifest.jsonl`. Dev dataset, seed 42: 11,671 matches and 76 exceptions, an exact match with the 76 manifest entries:

| Type | Count |
|---|---:|
| `FEE_MISMATCH` | 19 |
| `SETTLEMENT_LATE` | 12 |
| `STALE_PRICE` | 12 |
| `MISSING_EXTERNAL` | 11 |
| `CASH_BREAK` | 9 |
| `DUPLICATE_EXTERNAL` | 8 |
| `FX_MISMATCH` | 5 |

A rerun inserts 0 matches and 0 exceptions.

## Storage

The `recon` schema (`recon_dev` for the dev dataset) holds `runs`, `match_results`, and `exceptions`. All three are append-only, enforced by triggers, and keyed by deterministic IDs. A stored row whose content differs from a rerun's is refused (`LoadConflictError`). The source evidence is never touched: reconciliation only reads.
