"""Orchestration support: business-day check, lineage archive, alerts."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from northstar.application.operations import (
    Alert,
    archive_lineage,
    is_ledger_business_day,
    record_alert,
)

from factories import xtse


class MemoryStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_if_absent(self, key: str, body: bytes) -> bool:
        if key in self.objects:
            return False
        self.objects[key] = body
        return True

    def get(self, key: str) -> bytes:
        return self.objects[key]


def test_business_days_are_ledger_settlement_days() -> None:
    assert is_ledger_business_day(xtse(), date(2026, 3, 2))
    assert not is_ledger_business_day(xtse(), date(2026, 3, 7))  # Saturday
    # Remembrance Day: the TSX trades, but the ledger (CAD settlement) is closed.
    assert not is_ledger_business_day(xtse(), date(2026, 11, 11))


def test_lineage_is_archived_once_and_must_be_openlineage_events(tmp_path: Path) -> None:
    store = MemoryStore()
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps({"eventType": "START"}) + "\n" + json.dumps({"eventType": "COMPLETE"}) + "\n",
        encoding="utf-8",
    )
    assert archive_lineage(store, events, "run-1") == "audit/lineage/run-1.jsonl"
    assert store.objects["audit/lineage/run-1.jsonl"] == events.read_bytes()
    with pytest.raises(FileExistsError, match="run-1"):
        archive_lineage(store, events, "run-1")
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="no lineage events"):
        archive_lineage(store, empty, "run-2")
    other = tmp_path / "other.jsonl"
    other.write_text(json.dumps({"hello": "world"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1 is not an OpenLineage run event"):
        archive_lineage(store, other, "run-3")
    assert set(store.objects) == {"audit/lineage/run-1.jsonl"}


def test_an_alert_is_logged_and_stored_once(caplog: pytest.LogCaptureFixture) -> None:
    store = MemoryStore()
    alert = Alert(
        "northstar_daily", "ingest", "manual__x", 3, "boom", datetime(2026, 1, 7, tzinfo=UTC)
    )
    key = record_alert(store, alert)
    assert key == "audit/alerts/northstar_daily/manual__x/ingest-try3.json"
    stored = json.loads(store.objects[key])
    assert stored == {
        "dag_id": "northstar_daily",
        "task_id": "ingest",
        "run_id": "manual__x",
        "try_number": 3,
        "message": "boom",
        "raised_at": "2026-01-07T00:00:00+00:00",
    }
    assert any(r.levelname == "ERROR" and "boom" in r.getMessage() for r in caplog.records)
    with pytest.raises(FileExistsError):
        record_alert(store, alert)
