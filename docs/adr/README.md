# Architecture decision records

An ADR records one significant decision: its context, the options considered, the choice made, and the consequences. ADRs are never edited to change a decision. A new ADR supersedes the old one, and the old one's status changes to `Superseded by NNN`.

| ADR | Title | Status |
|---|---|---|
| [001](001-platform-architecture.md) | Modular monolith in a domain-driven monorepo | Accepted |
| [002](002-ledger-system-of-record.md) | Double-entry ledger as the financial system of record | Accepted |
| [003](003-batch-first-processing.md) | Batch-first processing | Accepted |
| [004](004-fixed-precision-money.md) | Fixed-precision decimal money | Accepted |
| [005](005-local-postgres-cloud-redshift.md) | Local PostgreSQL, cloud Redshift, dbt Core 1.x | Accepted |
| [006](006-bitemporal-records-and-restatements.md) | Bitemporal records and versioned restatements | Accepted |
| [007](007-single-owner-for-each-calculation.md) | Single owner for each calculation | Accepted |
| [008](008-data-lineage-and-observability.md) | OpenLineage and OpenTelemetry for lineage and observability | Accepted |
| [009](009-local-telemetry-backend.md) | Local telemetry backend: OpenTelemetry Collector with the debug exporter | Accepted |
| [010](010-cloud-batch-runtime.md) | Cloud batch runtime: EventBridge Scheduler, Step Functions, and ECS Fargate | Accepted |
| [011](011-looker-studio-dashboards.md) | Dashboards in Looker Studio, reading serving views of the published snapshots | Accepted |
| [012](012-market-risk-models.md) | Market risk models: historical simulation on a synthetic history, and model-consistent options | Accepted |
| [013](013-corporate-events.md) | Corporate events: cash dividends, and option exercise and expiry | Accepted |

New ADRs copy [template.md](template.md) and take the next number.
