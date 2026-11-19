# Northstar dbt project

This is the warehouse transformation layer for Northstar Brokerage Intelligence (module M1, Data Foundation).

**Status:** I scaffolded the folders with `dbt init` and haven't written any models yet. I deleted the starter example models that `dbt init` generates, because they were placeholders.

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
| dbt-redshift | not installed | Added in Phase 3 |

dbt is now pinned in the `dbt` dependency group of the root `pyproject.toml` and locked in `uv.lock`. Run it through `uv` from the repository root, for example `make dbt-parse`, rather than through ad-hoc `uvx` calls.

## Profile setup

1. Copy `profiles.yml.example` to `~/.dbt/profiles.yml`, or point `--profiles-dir` at a private directory.
2. Export `NBI_PG_USER` and `NBI_PG_PASSWORD`, plus the other `NBI_PG_*` variables if the defaults don't apply.
3. Run `dbt debug` to check the connection. A local Postgres server is needed, and the Phase 1 Docker Compose file will provide it.

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
