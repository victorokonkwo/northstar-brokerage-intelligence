# Data foundation

| | |
|---|---|
| Status | Partly implemented (Phase 3): ingestion into the raw layer and the warehouse load (3B). The dbt models, snapshots, and orchestration follow in 3C–3G ([roadmap](../project/roadmap.md#phase-3-weeks-57)). |
| Requirements | FR-M1-01 to FR-M1-03 ([requirements](../business/requirements.md)) |

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
