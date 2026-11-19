# 009. Local telemetry backend: OpenTelemetry Collector with the debug exporter

| | |
|---|---|
| Status | Accepted |
| Date | 2026-11-19 |
| Deciders | Project owner |

## Context

[ADR 008](008-data-lineage-and-observability.md) adopted OpenTelemetry and OpenLineage. It left the local-development telemetry backend and the OpenLineage backend unselected, and it requires a separate approved decision before any such service is added. The Phase 1 roadmap includes an OpenTelemetry foundation. Pipeline-level OpenLineage events are scheduled for Phase 3.

The local stack already runs Postgres twice, Airflow, and SeaweedFS on a developer machine with about 6 GB of memory for Docker.

## Options considered

1. **OpenTelemetry Collector (contrib), debug exporter only.** One small container that receives OTLP and prints what it receives. There is no UI. It is the same component that will forward telemetry to CloudWatch over OTLP in AWS (ADR 008), so the local and cloud paths match.
2. **`grafana/otel-lgtm`.** Collector, Prometheus, Tempo, Loki, and Grafana in one container, with a UI to browse telemetry. It is considerably heavier next to Airflow.
3. **Marquez now, for OpenLineage.** This is the OpenLineage reference backend. It needs its own API, web, and database containers, and nothing emits lineage before Phase 3.

## Decision

1. The local telemetry backend is **`otel/opentelemetry-collector-contrib:0.165.0`**, configured in `infrastructure/docker/otel-collector.yaml`. It has an OTLP/HTTP receiver on port 4318, the `memory_limiter` and `batch` processors, the **debug exporter** for traces, metrics, and logs, and the `health_check` extension on port 13133. Both ports are published on `127.0.0.1` only.
2. Application code uses the OpenTelemetry **API** only. Entry points configure the SDK (`northstar.infrastructure.telemetry`), and they export only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. The domain and common packages must not import `opentelemetry`, `psycopg`, or `yaml`; an import-linter contract enforces this.
3. The **OpenLineage backend is decided in Phase 3**, together with the Airflow and dbt OpenLineage integrations.

## Consequences

- Telemetry is visible locally with `docker compose logs otel-collector`, and CI checks that the e2e run's spans, metrics, and logs arrive.
- There is no telemetry UI. Adding one (for example option 2) needs a new decision.
- The Python logs signal uses the SDK's `_logs` modules, which OpenTelemetry Python still names as experimental (`opentelemetry.sdk._logs`). An SDK upgrade may need code changes there.
- Log records carry OpenTelemetry's `code.file.path` attribute, which shows the absolute source path on the machine that ran the code.

## Revisit when

A UI is needed to investigate telemetry locally, the Collector's resource use becomes a problem, or Phase 3 adds lineage emitters.
