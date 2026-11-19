"""Tests for telemetry settings, provider construction, and JSON log lines."""

import json
import logging
import sys

from opentelemetry.sdk.trace import TracerProvider

from northstar.infrastructure.telemetry import (
    JsonFormatter,
    TelemetrySettings,
    build_telemetry,
)


def _record(message: str = "loaded", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("northstar.test", logging.INFO, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


class TestSettings:
    def test_endpoint_and_environment_from_env(self) -> None:
        settings = TelemetrySettings.from_env(
            {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4318", "NBI_ENVIRONMENT": "ci"}
        )
        assert settings == TelemetrySettings("http://localhost:4318", "ci")

    def test_missing_or_empty_endpoint_disables_export(self) -> None:
        assert TelemetrySettings.from_env({}).otlp_endpoint is None
        assert TelemetrySettings.from_env({"OTEL_EXPORTER_OTLP_ENDPOINT": ""}).otlp_endpoint is None
        assert TelemetrySettings.from_env({}).environment == "local"


def test_without_an_endpoint_nothing_is_exported() -> None:
    telemetry = build_telemetry(TelemetrySettings(None, "test"), "0.0.0")
    try:
        assert not telemetry.exporting
        resource = telemetry.tracer_provider.resource.attributes
        assert resource["service.name"] == "northstar"
        assert resource["service.version"] == "0.0.0"
        assert resource["deployment.environment.name"] == "test"
    finally:
        telemetry.shutdown()


class TestJsonFormatter:
    def test_one_json_object_with_context_fields(self) -> None:
        line = json.loads(JsonFormatter().format(_record(job="run-slice", run_id="r1", seed=42)))
        assert line["message"] == "loaded"
        assert line["level"] == "INFO"
        assert (line["job"], line["run_id"], line["seed"]) == ("run-slice", "r1", 42)
        assert line["timestamp"].endswith("+00:00")  # UTC (CONTRIBUTING.md W3)
        assert "trace_id" not in line

    def test_unknown_extra_fields_are_not_logged(self) -> None:
        line = json.loads(JsonFormatter().format(_record(password="hunter2")))  # noqa: S106
        assert "password" not in line

    def test_trace_correlation_inside_a_span(self) -> None:
        tracer = TracerProvider().get_tracer("test")
        with tracer.start_as_current_span("work") as span:
            line = json.loads(JsonFormatter().format(_record()))
            context = span.get_span_context()
        assert line["trace_id"] == format(context.trace_id, "032x")
        assert line["span_id"] == format(context.span_id, "016x")

    def test_exceptions_are_included(self) -> None:
        try:
            raise ValueError("boom")  # noqa: TRY301 - the case under test
        except ValueError:
            record = _record()
            record.exc_info = sys.exc_info()
        assert "ValueError: boom" in json.loads(JsonFormatter().format(record))["exception"]
