"""Shared pytest configuration.

Hypothesis profiles (select with the HYPOTHESIS_PROFILE environment variable):

- ``dev`` (default): random examples, so local runs keep exploring new inputs.
- ``ci``: derandomized, so a CI run is reproducible from the commit alone
  (CONTRIBUTING.md W4).
"""

import os

import pytest
from hypothesis import settings
from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

settings.register_profile("dev")
settings.register_profile("ci", derandomize=True, print_blob=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))


@pytest.fixture(scope="session")
def otel_memory() -> tuple[InMemorySpanExporter, InMemoryMetricReader]:
    """Install in-memory global OpenTelemetry providers once per test process.

    OpenTelemetry allows global providers to be set only once, so tests that
    assert on emitted telemetry share these. Clear the span exporter before use.
    """
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(MeterProvider(metric_readers=[reader]))
    return spans, reader
