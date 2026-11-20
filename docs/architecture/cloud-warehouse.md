# Cloud warehouse: S3 bronze and Redshift Serverless

| | |
|---|---|
| Status | Phase 4F-a: loading and dbt on Redshift. Reconciliation and publication on Redshift follow in 4F-c, and the scheduled cloud run (Step Functions and ECS Fargate) in 4G |
| Decisions | [ADR 005](../adr/005-local-postgres-cloud-redshift.md) (local Postgres, cloud Redshift); owner decisions 2026-11-20: Step Functions and Fargate for the cloud batch; Redshift controls by grants, the application, and dbt tests |
| Infrastructure | [Terraform data stack](../../infrastructure/terraform/data/README.md) |
| Facts | [sources.md](../sources.md): the Redshift and S3 rows dated 2026-11-20 |

The same pipeline code runs against both warehouses. `--warehouse postgres` (the default) uses the local stack: SeaweedFS and Postgres. `--warehouse redshift` uses the S3 bronze bucket and Redshift Serverless.

## Flow

```
ingest ─► s3://northstar-bronze-…/raw/…   content-addressed, put-if-absent (bronze)
load-run ─► s3://…/_stage/<schema>.<table>/<sha256>.jsonl ─► COPY ─► temp table
          ─► conflict check ─► INSERT … WHERE NOT EXISTS ─► raw_dev, ledger_dev
dbt build (target redshift) ─► dbt_dataset (silver and gold; incremental, see incremental-models.md)
```

- **Ingest is unchanged.** It writes the same content-addressed objects to S3. AWS S3 refuses an overwrite just as SeaweedFS does: re-ingesting wrote 0 of 929 objects again.
- **Loading has the same contract on both warehouses** (`insert_new`): new keys are inserted, a replay inserts nothing, and a stored row with different content is refused (`LoadConflictError`).
  - Postgres uses `COPY FROM STDIN` and `ON CONFLICT`.
  - Redshift has neither, so the rows are staged as JSON Lines under `_stage/`, which is content-addressed so a replay reuses the file. They are then copied into a temp table, checked null-safely (Redshift has no `IS DISTINCT FROM`), and inserted where the key is new.
  - Journal lines are posted by Python and loaded the same way.
- **No passwords.** The pipeline and dbt connect with temporary IAM credentials (`redshift-serverless get-credentials`), as the caller's IAM database user. Only `redshift-bootstrap` uses the admin, whose password Redshift keeps in Secrets Manager.

## Controls on Redshift

Redshift has no triggers and no CHECK constraints, and doesn't enforce unique keys. The owner decided on 2026-11-20 that the controls Postgres enforces in the database are enforced on Redshift as follows:

| Control | Postgres | Redshift |
|---|---|---|
| Append-only raw, ledger, and recon tables (W2.4, W3.3) | triggers refuse UPDATE, DELETE, TRUNCATE | the admin owns the tables. The pipeline's role (`nbi_pipeline`) holds only USAGE, SELECT, and INSERT, so UPDATE, DELETE, TRUNCATE, and LOCK are refused. Proven as the pipeline user by `make test-redshift` |
| Lifecycle rules on transitions (FR-M2-05, FR-M2-06) | CHECK and UNIQUE constraints | the domain's `check()` before every write, plus `assert_exception_transitions_follow_the_lifecycle` (dbt, both warehouses), which returns any stored row that breaks a rule |
| Journal line constraints (ADR 002) | CHECK and UNIQUE constraints | `insert_new`, plus `assert_journal_lines_meet_ledger_constraints` (dbt, both warehouses) |
| One manual change at a time per exception | `SELECT … FOR UPDATE` | `LOCK` on an empty `lifecycle_lock` table. LOCK needs UPDATE, which the pipeline holds on that table only |

`make test-redshift` also inserts one row per broken rule on Redshift, where nothing refuses them, and checks that each dbt test returns exactly those rows: 10 transitions and 5 journal lines. Removing a rule from either test makes it fail.

## dbt on both warehouses

`profiles.yml` has a `redshift` target (`NBI_DBT_TARGET=redshift`) next to `local`. The differences are handled by the macros in `warehouse/dbt/macros/dialect.sql`:

| Need | Macro | Postgres | Redshift |
|---|---|---|---|
| a field in a JSON payload | `json_text`, `json_value` | `payload -> 'a' ->> 'b'` | SUPER path `payload."a"."b"` |
| an array of JSON strings, one row each | `json_array_texts` | `jsonb_array_elements_text` | PartiQL in `FROM` |
| strings | `string_type()` | `text` | `varchar(4096)` (`text` would be varchar(256)) |
| `a is distinct from b` | `distinct_from` | portable null-safe form | same |

- **No bare `numeric`.** Redshift reads `numeric` as numeric(18, 0) and rounds the decimals away; porting found KPI-01's daily totals rounded to whole dollars that way, and they're fixed. Every cast and contract type states its precision.
- **The guard.** `tests/unit/test_dbt_portability.py` fails CI on bare `numeric`, `::text`, jsonb operators and functions, `IS DISTINCT FROM`, `FILTER`, `DISTINCT ON`, `LATERAL`, `generate_series`, and unquoted comma types in YAML. CI never runs Redshift, so this guard keeps it in reach.
- **Parsing both targets.** `make dbt-parse` parses both targets.
- **Incremental models.** The incremental models and their `rebuild` strategy run unchanged on both warehouses ([incremental models](incremental-models.md)). `tests/redshift/test_redshift_incremental.py` runs the late-data scenario live.

## Parity with Postgres

`make compare-warehouses` compares every model in `dbt_dataset` between the two warehouses, row for row. It leaves out only the lineage columns, which identify a run rather than the data.

| Run | Result |
|---|---|
| Commit `fee1f1a`, 2026-11-20. The seed-42 dev dataset (`make simulate`) plus the late-deposit fixture, loaded into both warehouses. `make dbt-build-dataset` and `make dbt-build-redshift`: 241 of 241 pass on each. Then `make compare-warehouses` | **41 of 49 models identical**. The other 8 are the reconciliation models (`stg_recon__*`, `fct_reconciliation_exceptions`, and the KPI-02, -03/-04, and -05 marts), which are empty on Redshift until reconciliation runs there (4F-c) |

**Known differences that don't change results:**
- **Division precision.** It keeps 16 decimal places on Redshift against 20 on Postgres. The rate KPIs round to 6 (half-even), and the comparison shows no difference.
- **`_ingested_at` precision.** Redshift stores whole seconds. This is lineage only.
- **SUPER values.** They come back as JSON text, not parsed JSON. The comparison normalises the form, never the value.

## Commands

| Target | What it does |
|---|---|
| `make redshift-bootstrap` | as the admin, creates the dev schemas' append-only tables and grants your IAM database user the pipeline role |
| `northstar --warehouse redshift ingest …` / `load-run …` | ingest to S3 bronze; load into Redshift |
| `make dbt-build-redshift` | `dbt build` on Redshift into `dbt_dataset` |
| `make compare-warehouses` | Postgres vs Redshift, every model |
| `make test-redshift` | loading, append-only, lock, and dbt-check controls, live (costs a few cents) |

Each needs `aws login`. The endpoint, bucket, and secret ARN contain the account ID, so they are read from `terraform output` at run time (`scripts/redshift_env.py`) and never committed.
