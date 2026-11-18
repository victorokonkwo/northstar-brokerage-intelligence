# 005. Local PostgreSQL, cloud Redshift, dbt Core 1.x

| | |
|---|---|
| Status | Accepted |
| Date | 2026-11-18 |
| Deciders | Project owner |

## Context

- Development and CI should be fast, free, and runnable offline.
- The portfolio should demonstrate a cloud warehouse on AWS. The candidates were Snowflake and Amazon Redshift, and AWS is the primary cloud.
- dbt is the transformation tool. During setup on 2026-11-18, the dbt CLI reported that **dbt v2 is now the default for new installations**, with v1 still available. dbt's upgrade guide says a project must run cleanly on the latest v1.x before moving to v2. We have not confirmed whether v2 supports the Postgres and Redshift adapters yet.

## Options considered

1. **Redshift only.** One SQL dialect, but every dev and CI run costs money and needs the network.
2. **PostgreSQL locally, Redshift Serverless in the cloud, dbt as the abstraction.** Cheap and fast locally. Redshift diverges from PostgreSQL in places (e.g., supported functions, constraints not enforced, distribution and sort keys), so cross-database care is needed.
3. **Snowflake.** A strong warehouse, but outside AWS-native IAM and networking, which weakens the AWS story.

For dbt:

a. **dbt v2 now.** Latest, but adapter support for our targets is unverified.
b. **dbt Core 1.12.x with dbt-postgres** (verified working locally on 2026-11-18: dbt Core 1.12.7, dbt-postgres 1.11.0, Python 3.12), keeping the project clean of deprecation warnings so a v2 migration stays possible.

## Decision

Option 2 with dbt option b.

- Local and CI target: PostgreSQL via `dbt-postgres`.
- Cloud target: Redshift Serverless via `dbt-redshift`, added in Phase 3.
- The dbt project was created with `dbt init` (see `warehouse/dbt/README.md`). New dbt projects are created the same way.
- Use dbt cross-database macros or project macros wherever dialects differ. Never write raw dialect-specific SQL in models without a macro.
- Redshift does not enforce primary key or unique constraints, so uniqueness is always guaranteed by dbt tests, never by the database.

## Consequences

- CI runs dbt against a disposable PostgreSQL container.
- A Redshift-specific test pass is needed before each cloud release, because some differences only show up there.
- dbt v1 → v2 migration is a tracked risk (risk register R-11).

## Revisit when

dbt v2 adapter support for Postgres and Redshift is confirmed from dbt's official documentation, or Redshift costs exceed the budget.
