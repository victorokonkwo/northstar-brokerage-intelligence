"""The slice emits spans and counters through the OpenTelemetry API."""

from pathlib import Path

from opentelemetry.sdk.metrics.export import InMemoryMetricReader, Sum
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from northstar.application.sprint1 import prepare_slice

from factories import CONFIG

SCENARIO = CONFIG / "simulation" / "sprint1_scenario.yaml"


def _counter(reader: InMemoryMetricReader, name: str) -> int:
    """Current cumulative value of a counter (0 before anything is recorded)."""
    data = reader.get_metrics_data()
    if data is None:
        return 0
    total = 0
    for resource_metrics in data.resource_metrics:
        for scope in resource_metrics.scope_metrics:
            for metric in scope.metrics:
                if metric.name == name and isinstance(metric.data, Sum):
                    total += sum(int(point.value) for point in metric.data.data_points)
    return total


def test_prepare_slice_emits_a_span_and_counters(
    otel_memory: tuple[InMemorySpanExporter, InMemoryMetricReader], tmp_path: Path
) -> None:
    spans, reader = otel_memory
    spans.clear()
    generated_before = _counter(reader, "northstar.events.generated")
    posted_before = _counter(reader, "northstar.journal_batches.posted")

    prepared = prepare_slice(CONFIG, SCENARIO, 42, tmp_path)

    (span,) = [s for s in spans.get_finished_spans() if s.name == "northstar.prepare_slice"]
    assert span.attributes is not None
    assert span.attributes["northstar.scenario_id"] == "sprint1-cad-buy"
    assert span.attributes["northstar.seed"] == 42
    assert span.attributes["northstar.events.count"] == 7
    assert span.attributes["northstar.events.sha256"] == prepared.events_sha256
    assert _counter(reader, "northstar.events.generated") - generated_before == 7
    assert _counter(reader, "northstar.journal_batches.posted") - posted_before == 4
