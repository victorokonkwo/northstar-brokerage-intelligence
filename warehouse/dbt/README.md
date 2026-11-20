# Northstar dbt project

This is the warehouse transformation layer for Northstar Brokerage Intelligence (module M1, Data Foundation).

**Status:** created with `dbt init` (the generated example models were deleted). It holds the staging, intermediate, core, and mart models of Phases 3 and 4, with contracts on core and marts, and runs on Postgres and Redshift ([cloud warehouse](../../docs/architecture/cloud-warehouse.md)). The core facts, dimensions, and daily marts are incremental: a build rewrites only what new or late records change ([incremental models](../../docs/architecture/incremental-models.md)).

## How this project was created

```bash
# Run from warehouse/. Pinned to Python 3.12 because dbt Core 1.x
# does not support the newest CPython releases yet.
uvx --python 3.12 --from dbt-core==1.12.7 --with dbt-postgres==1.11.0 \
    dbt init northstar --skip-profile-setup
mv northstar dbt              # repo convention: warehouse/dbt/
rm -rf dbt/models/example     # starter demo models, not project code
```

Use the same approach for new dbt projects or packages. Run `dbt init` and keep the layout it generates. Don't create the dbt skeleton by hand.

## Versions

| Component | Version | Notes |
|---|---|---|
| dbt Core | 1.12.7 | dbt v2 is now dbt's default for new installs. We stay on v1.x until v2 adapter support for Postgres and Redshift is confirmed. See [ADR 005](../../docs/adr/005-local-postgres-cloud-redshift.md). |
| dbt-postgres | 1.11.0 | Local development target |
| dbt-redshift | 1.11.2 | Cloud target (`NBI_DBT_TARGET=redshift`), added in Phase 4F-a |

dbt is now pinned in the `dbt` dependency group of the root `pyproject.toml` and locked in `uv.lock`. Run it through `uv` from the repository root, for example `make dbt-parse`, rather than through ad-hoc `uvx` calls.

## Profile setup

`profiles.yml` in this folder is committed. It contains no credentials: every value is an `env_var()` read from the root `.env` (`make env`). dbt finds it because it runs from `warehouse/dbt/`.

From the repository root:

| Command | What it does |
|---|---|
| `make up` | starts the warehouse Postgres (and the rest of the local stack) |
| `make dbt-parse` | parses the project (no database needed) |
| `make dbt-build` | builds and tests every model against the local warehouse |
| `make sql-lint` | SQLFluff with the dbt templater. It needs the running database, because the templater compiles through dbt |
| `make dbt-build-full` / `make compare-incremental` | full-refresh build into `dbt_dataset_full`, then every model compared with the incremental `dbt_dataset`, row for row |

The raw and ledger tables are created and filled by the Python loader (`northstar.infrastructure.postgres`).

## Layout rules

Follow [`docs/engineering/engineering-standards.md`](../../docs/engineering/engineering-standards.md) section 3. In short:

```
models/
  sources/        source definitions (yml only)
  staging/        stg_<source>__<entity>: 1:1 with source, rename/cast only
  intermediate/   int_<entity>_<verb>: business logic building blocks
  core/           dim_* / fct_*: canonical model, contracts enforced
  marts/<domain>/ mart_*: consumer-facing, one folder per module
```

Each model's YAML states its grain and primary key, and the key is tested for `unique` and `not_null`. Monetary columns use `numeric`, never `float`. Financial invariant tests live in `tests/` as singular tests named `assert_<invariant>.sql`.

## Serving views

`models/serving/` holds what dashboards read ([ADR 011](../../docs/adr/011-looker-studio-dashboards.md)): views of each date's currently published snapshot. They read the published schemas, which exist only after publication, so `selectors.yml` leaves `tag:serving` out of every build that doesn't select it; `make serving` builds them after `make publish`.

## Incremental models

An incremental model uses the `rebuild` strategy and declares in `meta.rebuild` whether it rebuilds by date or by key, and which staging models trigger it. `tests/unit/test_dbt_incremental.py` fails if that list differs from what the model reads. A change to a model's own SQL file rebuilds it in full on its next build. A change to anything it reads (a staging view, an intermediate model, a macro) or to a contract needs `dbt build --full-refresh`. See [incremental models](../../docs/architecture/incremental-models.md).
