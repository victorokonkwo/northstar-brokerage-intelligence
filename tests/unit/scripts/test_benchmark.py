"""The benchmark's reporting: units, summaries, and writing between the doc markers."""

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).resolve().parents[3]


def _benchmark() -> ModuleType:
    spec = importlib.util.spec_from_file_location("benchmark", REPO / "scripts" / "benchmark.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_peak_memory_units_follow_the_platform() -> None:
    b = _benchmark()
    assert b.peak_rss_mib(3 * 1024 * 1024, "Darwin") == 3  # bytes
    assert b.peak_rss_mib(3 * 1024, "Linux") == 3  # kibibytes


def test_summaries_keep_size_lines_and_drop_json_logs() -> None:
    b = _benchmark()
    output = (
        '{"level": "INFO", "message": "simulated"}\n'
        "dataset load seed 42: 559694 events under data/generated/load/seed=42\n"
        "  event trade_executed 98334\n"
        "  sha256 abc canonical/events.jsonl\n"
    )
    assert b.summarize("simulate", output) == (
        "dataset load seed 42: 559694 events under data/generated/load/seed=42 / "
        "event trade_executed 98334"
    )
    assert b.summarize("publish", '{"x": 1}\n') == "(no summary line)"


def test_results_replace_only_the_marked_section(tmp_path: Path) -> None:
    b = _benchmark()
    doc = tmp_path / "load-test.md"
    doc.write_text(f"# Title\n\nintro\n\n{b.START}\nold\n{b.END}\n\noutro\n", encoding="utf-8")
    result = b.StageResult("ingest", "northstar ingest", 12.34, 456.7, 0, "run x SUCCEEDED")
    section = b.render([result], {"date": "2026-11-19 00:00 UTC", "Machine": "arm64"})
    b.write_doc(doc, section)
    text = doc.read_text(encoding="utf-8")
    assert text.startswith("# Title\n\nintro\n\n")
    assert text.endswith("\n\noutro\n")
    assert "old" not in text
    assert "| ingest | 12.3 | 457 | ok: run x SUCCEEDED |" in text
    assert "Total wall time: 12.3 s." in text


def test_a_doc_without_markers_is_refused(tmp_path: Path) -> None:
    b = _benchmark()
    doc = tmp_path / "plain.md"
    doc.write_text("# no markers\n", encoding="utf-8")
    with pytest.raises(ValueError, match="markers"):
        b.write_doc(doc, "x")


def test_summaries_drop_terminal_colour_codes() -> None:
    b = _benchmark()
    output = "\x1b[0m01:20:03  Done. PASS=174 WARN=0 ERROR=0\n"
    assert b.summarize("dbt build", output) == "01:20:03  Done. PASS=174 WARN=0 ERROR=0"
