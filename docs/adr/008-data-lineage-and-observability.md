# 008. OpenLineage and OpenTelemetry for lineage and observability

| | |
|---|---|
| Status | Accepted |
| Date | 2026-11-18 |
| Deciders | Project owner |

## Context

The platform needs end-to-end visibility into pipeline dependencies and operational health. Row-level lineage and structured logs are useful, but they do not provide a common event format for pipeline lineage or correlated traces, metrics, and logs.

## Decision

1. Use **OpenLineage** events for pipeline-level job, run, and dataset lineage. Integrate with Airflow and dbt where supported. This supplements, and does not replace, source IDs, audit columns, run manifests, dbt documentation, or financial tie-out controls.
2. Use **OpenTelemetry** instrumentation for application and pipeline traces, metrics, and logs. In AWS environments, export telemetry to **Amazon CloudWatch** using OpenTelemetry Protocol (OTLP).
3. Keep structured JSON logs with `run_id`, `business_date`, and job name. Telemetry must not contain secrets, full source payloads, or personal-like fields.
4. The OpenLineage event backend and local-development telemetry backend are not selected here. Select them before implementing the corresponding integrations; do not add a service without a separate approved decision.
5. CloudWatch resources and permissions are managed through Terraform, after the AWS budget alerts required by W6 are in place.

## Consequences

- Lineage and telemetry use standard formats and can be routed to compatible backends.
- OpenLineage lineage describes pipeline inputs and outputs; it does not prove financial correctness. Existing ledger controls, row-level lineage, and invariant tests remain authoritative.
- Export configuration, telemetry retention, CloudWatch alarms, and the OpenLineage backend must be defined before the relevant integrations are delivered.

## Revisit when

The selected OpenLineage backend or CloudWatch telemetry path cannot meet the platform's security, retention, or cost requirements.
