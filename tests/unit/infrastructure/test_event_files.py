"""Tests for canonical JSON Lines event files."""

import json
from pathlib import Path

import pytest

from northstar.infrastructure.event_files import read_events, serialize_event, write_events

from factories import deposit, execution, settlement


def test_round_trip(tmp_path: Path) -> None:
    events = (deposit(), execution(), settlement())
    path = tmp_path / "nested" / "events.jsonl"
    assert write_events(path, events) == 3
    assert read_events(path) == events


def test_canonical_form(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    write_events(path, [execution()])
    raw = path.read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r" not in raw
    assert b": " not in raw  # no insignificant whitespace
    line = raw.decode().rstrip("\n")
    assert line == json.dumps(json.loads(line), sort_keys=True, separators=(",", ":"))
    payload = json.loads(line)["payload"]
    assert payload["price"] == "250.00"  # decimals as strings, never JSON numbers
    assert payload["quantity"] == "10"


def test_serialization_is_stable() -> None:
    assert serialize_event(execution()) == serialize_event(execution())


def test_invalid_line_reports_its_line_number(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        serialize_event(deposit()) + "\n" + '{"event_type": "cash_deposited"}\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match=r"events\.jsonl:2: invalid event"):
        read_events(path)
