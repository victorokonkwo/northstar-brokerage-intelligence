"""OpenTelemetry setup and structured JSON logging (ADR 008).

Process entry points call ``configure_telemetry`` once and ``shutdown`` at
exit. Everything else uses the vendor-neutral OpenTelemetry *API*
(``trace.get_tracer``, ``metrics.get_meter``) and stdlib ``logging``, so the
domain never depends on how telemetry is exported.

Export happens only when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set (the standard
OpenTelemetry variable; locally ``http://localhost:4318``, the Collector from
``make up``). Without it, telemetry is recorded but not exported, and a log
line says so.

Never put secrets, full payloads, or personal-like fields in span attributes,
metric attributes, or log fields.
"""

import json
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from opentelemetry import _logs, metrics, trace
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

SERVICE_NAME: Final = "northstar"

CONTEXT_FIELDS: Final = ("job", "run_id", "business_date", "scenario_id", "seed")
"""Log record attributes copied into every JSON log line when present."""


@dataclass(frozen=True, slots=True)
class TelemetrySettings:
    """Where telemetry goes and how the service identifies itself."""

    otlp_endpoint: str | None
    environment: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "TelemetrySettings":
        """Read ``OTEL_EXPORTER_OTLP_ENDPOINT`` and ``NBI_ENVIRONMENT`` (default ``local``)."""
        env = os.environ if environ is None else environ
        return cls(
            otlp_endpoint=env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or None,
            environment=env.get("NBI_ENVIRONMENT", "local"),
        )


@dataclass(frozen=True, slots=True)
class Telemetry:
    """The configured providers. Call ``shutdown`` once, at process exit."""

    tracer_provider: TracerProvider
    meter_provider: MeterProvider
    logger_provider: LoggerProvider
    exporting: bool

    def shutdown(self) -> None:
        """Flush and stop every provider, so short-lived commands lose nothing."""
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()
        self.logger_provider.shutdown()


class JsonFormatter(logging.Formatter):
    """Formats a log record as one JSON object with trace correlation."""

    def format(self, record: logging.LogRecord) -> str:
        """Return the record as a JSON line (keys sorted)."""
        line: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in CONTEXT_FIELDS:
            if hasattr(record, field):
                line[field] = getattr(record, field)
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            line["trace_id"] = format(span_context.trace_id, "032x")
            line["span_id"] = format(span_context.span_id, "016x")
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, sort_keys=True, default=str)


def build_telemetry(settings: TelemetrySettings, service_version: str) -> Telemetry:
    """Create tracer, meter, and logger providers without installing them.

    Exporters are attached only when an OTLP endpoint is configured. They read
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` themselves and append the per-signal paths
    (``/v1/traces``, ``/v1/metrics``, ``/v1/logs``). Building opens no
    connection.
    """
    resource = Resource.create(
        {
            "service.name": SERVICE_NAME,
            "service.version": service_version,
            "deployment.environment.name": settings.environment,
        }
    )
    exporting = settings.otlp_endpoint is not None

    tracer_provider = TracerProvider(resource=resource)
    readers: list[MetricReader] = []
    logger_provider = LoggerProvider(resource=resource)
    if exporting:
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        readers.append(PeriodicExportingMetricReader(OTLPMetricExporter()))
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    return Telemetry(tracer_provider, meter_provider, logger_provider, exporting)


def install_telemetry(telemetry: Telemetry) -> None:
    """Make ``telemetry`` the process-wide providers and route logging to it.

    OpenTelemetry allows global providers to be set once per process, so only
    entry points call this. The root logger writes JSON lines to stderr and
    forwards records to the OpenTelemetry logger provider.
    """
    trace.set_tracer_provider(telemetry.tracer_provider)
    metrics.set_meter_provider(telemetry.meter_provider)
    _logs.set_logger_provider(telemetry.logger_provider)
    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [stderr, LoggingHandler(logger_provider=telemetry.logger_provider)]
    root.setLevel(logging.INFO)
    if not telemetry.exporting:
        logging.getLogger(__name__).info(
            "telemetry export disabled: OTEL_EXPORTER_OTLP_ENDPOINT is not set"
        )


def configure_telemetry(settings: TelemetrySettings, service_version: str) -> Telemetry:
    """Build and install telemetry for a process entry point."""
    telemetry = build_telemetry(settings, service_version)
    install_telemetry(telemetry)
    return telemetry
