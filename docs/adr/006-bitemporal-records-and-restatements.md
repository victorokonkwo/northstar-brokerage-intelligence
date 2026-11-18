# 006. Bitemporal records and versioned restatements

| | |
|---|---|
| Status | Accepted |
| Date | 2026-11-18 |
| Deciders | Project owner |

## Context

Trades and cash records arrive late. External parties send corrections. Defects get found after a day's figures are published. If corrections overwrite published history, the platform can no longer answer "what did we report on date X, and why did it change?". That is a basic expectation for financial reporting.

## Options considered

1. **Overwrite in place** (merge or upsert). Simple, but it destroys published history.
2. **Bitemporal records plus versioned snapshots.** Every record carries when the fact happened (event or effective time) and when the platform learned it (`recorded_at`, `_ingested_at`). Published outputs are immutable snapshot versions.

## Decision

Option 2.

1. Raw storage is append-only. Corrections arrive as new records that reference what they correct.
2. Core models keep both event time and knowledge time. Ledger corrections are reversals plus new postings ([ADR 002](002-ledger-system-of-record.md)).
3. Each business date's outputs belong to a **snapshot** with `snapshot_version`, status (`DRAFT`, `PUBLISHED`, `SUPERSEDED`), `published_at`, and, for versions after the first, a `restatement_reason`.
4. A late-arriving record for a published date produces a new snapshot version for that date. The prior version becomes `SUPERSEDED` but stays queryable.
5. Point-in-time queries ("as published at time T") are supported for KPI marts.

## Consequences

- Reproducibility (INV-09) and an audit trail of restatements.
- Incremental dbt models must be designed around versioning: append new versions rather than `merge` over published rows. This needs careful testing.
- Storage grows with restatements. That is acceptable at this scale.

## Revisit when

Storage or query cost from version history becomes material.
