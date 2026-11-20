# Reconciliation

| | |
|---|---|
| Status | Partly implemented (Phase 4): the policy (4A); staged matching and classification (4B); the lifecycle and audit trail (4C); the controls marts and the publication gate (4D). The cloud warehouse and the dashboard follow in 4E–4G ([roadmap](../project/roadmap.md#phase-4-weeks-89)). |
| Requirements | FR-M2-02 to FR-M2-08, NFR-03; KPI-02 to KPI-06 ([requirements](../business/requirements.md), [KPI dictionary](../business/kpi-dictionary.md)) |
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

## Lifecycle and audit trail (FR-M2-05, FR-M2-06)

Every exception moves through domain model §7.4's lifecycle:

```text
OPEN → INVESTIGATING → RESOLVED
OPEN → RESOLVED   (a rule only)
OPEN → ESCALATED  (SLA breached)
INVESTIGATING → ESCALATED → RESOLVED
```

`northstar.domain.lifecycle.check` refuses any other move. Every change is appended to `exception_transitions` with an actor, a reason, a time, and a sequence number; nothing is updated or deleted. `RESOLVED` is terminal, and every resolution needs its proof:

| Resolution | Actor | Used for |
|---|---|---|
| `AUTO_RULE` | `rule:<id>@<version>` | a named, versioned rule |
| `MANUAL_OVERRIDE` | `user:<name>`, with a reason | a person; they must move the exception to `INVESTIGATING` or `ESCALATED` first, because only a rule may resolve an `OPEN` exception |
| `SOURCE_CORRECTED` | `rule:source_corrected@1` | a later run no longer finds the break |

The database enforces the same rules with CHECK constraints: no `RESOLVED` without a type, no manual override by a rule, no rule resolution by a person, and no empty reason. A unique (exception, sequence) means two concurrent changes cannot both land.

**Rules applied after every run, all versioned:**
1. `detected@1` opens each new exception.
2. `source_corrected@1` resolves an unresolved exception, dated on or before the as-of date, that the run no longer finds.
3. `late_settlement_completed@1` resolves a `SETTLEMENT_LATE` whose bank line was matched as a timing difference: the cash has moved.
4. `sla_breach@1` escalates an unresolved exception open longer than its severity's SLA, counted in ledger business days (`business_days_open`; weekends and holidays don't count).

A rerun proposes nothing new, so it appends nothing. People act through `northstar exception transition --to … --actor user:<name> --reason …`, and `northstar exception history` prints the trail.

**Dev dataset (seed 42), as of 2026-04-01:**
- 76 opened
- 12 late settlements auto-resolved
- 54 escalated past their SLA
- 10 still open, because they are recent

## Controls marts (KPI-02 to KPI-06)

dbt models tagged `controls` rebuild after each reconciliation (`dbt build --select tag:controls`). Python owns the findings (ADR 007); dbt aggregates them.

| Mart | Grain | KPI |
|---|---|---|
| `mart_match_rates` | trade date × currency | KPI-02: executions matched by any rule ÷ all, by count and gross notional (from the journal) |
| `mart_exception_monitoring` | business date × reconciliation × type × severity | KPI-03: open count and open exposure (Σ \|impact CAD\|); KPI-04: open exceptions escalated past their SLA |
| `mart_settlement_fails` | contractual settlement date × currency × asset class | KPI-05: obligations the depository reported late (`SETTLEMENT_LATE`) or did not confirm, by count and value |
| `mart_cash_breaks` | reporting date × currency | KPI-06: GL 1000 against the bank statement, rolled forward. Late settlements in transit are explained timing differences; the rest is unexplained (target 0) |

`fct_reconciliation_exceptions` gives each exception's current status, resolution, and escalation from its transition trail. Tests tie each mart to its sources (`assert_controls_tie_out`): exception counts and exposure, matched executions, and the last cash balances against all GL 1000 lines and all bank lines.

On the dev dataset:
- 774 of 784 executions are matched.
- 13 of 416 obligations failed: 12 late, and 1 not confirmed.
- The last day's unexplained cash is CAD 15,559.81 and USD −9,782.32. That is exactly the signed sum of the nine dropped bank lines, recomputed from the defect-free records.

## Publication gate (FR-M2-08)

Publication has two streams, each in its own published schema:

| Stream | Marts | Gate |
|---|---|---|
| `financial` | the four trusted-data marts | KPI-01 = 0, **and** no unresolved CRITICAL or HIGH exception dated that day |
| `controls` | the four controls marts | KPI-01 = 0 only, so operations can always see what blocks the financial snapshot |

A blocked financial date stays `DRAFT`, and `publish` lists the exceptions that block it and exits 2. Once they are resolved (for example `exception transition --to RESOLVED --resolution MANUAL_OVERRIDE --actor user:<name> --reason …`), the next `publish` moves the same version to `PUBLISHED`. The content did not change, so no new version is created. `publish --all` reports every blocked date and still publishes the rest.

On the dev dataset (CI, seed 42): 13 of the 63 financial dates block on open exceptions, and 50 publish. Resolving 2026-03-02's escalated `CASH_BREAK` with a reasoned override publishes that date's v1. A late deposit that arrives with its bank line reconciles cleanly and restates 2026-03-02 as v2.

In Airflow, a blocked date fails the `publish` task, which retries and then alerts: publication is held until operations act.
