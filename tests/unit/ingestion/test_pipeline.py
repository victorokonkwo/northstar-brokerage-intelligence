"""Ingestion into the raw layer: validation, quarantine, dedup, partitioning, manifests."""

import hashlib
import json
import shutil
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from northstar.application.simulate import simulate_dataset
from northstar.domain.calendar import business_date
from northstar.domain.events import EventType
from northstar.infrastructure.config_loader import load_policy
from northstar.ingestion.pipeline import (
    IngestionManifest,
    QuarantinePolicy,
    QuarantineThresholdError,
    RunStatus,
    SourceFile,
    ingest,
    read_manifest,
)
from northstar.ingestion.sources import EXTERNAL_DOMAINS
from northstar.simulation.external import RECORD_MODELS
from northstar.simulation.history import HISTORY_MODELS

from factories import CONFIG, policies

NOW = datetime(2026, 4, 1, 6, 0, tzinfo=UTC)
LENIENT = QuarantinePolicy(version="test", illustrative=True, max_quarantine_rate=Decimal(1))


class MemoryStore:
    """An in-memory object store with the same put-if-absent contract as S3."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_if_absent(self, key: str, body: bytes) -> bool:
        if key in self.objects:
            return False
        self.objects[key] = body
        return True

    def get(self, key: str) -> bytes:
        return self.objects[key]


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("generated")
    return simulate_dataset(
        CONFIG,
        CONFIG / "simulation" / "datasets" / "dev.yaml",
        42,
        out,
        CONFIG / "simulation" / "defects" / "dev.yaml",
    ).root


def _ingest(
    store: MemoryStore, root: Path, run_id: str, policy: QuarantinePolicy = LENIENT
) -> IngestionManifest:
    return ingest(
        store, SourceFile.for_dataset(root), run_id, policy, policies().calendars, "test-sha", NOW
    )


def _lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").split("\n") if line]


class TestCleanDataset:
    def test_every_line_is_accepted_into_raw(self, dataset: Path) -> None:
        store = MemoryStore()
        manifest = _ingest(store, dataset, "r1")
        assert manifest.status is RunStatus.SUCCEEDED
        assert manifest.quarantine_rate == 0
        files = {Path(f.path).name: f for f in manifest.files}
        assert files["events.jsonl"].lines == len(_lines(dataset / "canonical" / "events.jsonl"))
        for f in manifest.files:
            assert (f.accepted, f.duplicates, f.quarantined) == (f.lines, 0, 0)
            assert f.sha256 == hashlib.sha256(Path(f.path).read_bytes()).hexdigest()
        assert sum(o.rows for o in manifest.raw_objects()) == sum(f.lines for f in manifest.files)

    def test_raw_is_the_input_byte_for_byte(self, dataset: Path) -> None:
        store = MemoryStore()
        manifest = _ingest(store, dataset, "r1")
        stored = sorted(
            line
            for o in manifest.raw_objects()
            for line in store.get(o.key).decode().split("\n")
            if line
        )
        inputs = sorted(line for f in manifest.files for line in _lines(Path(f.path)))
        assert stored == inputs

    def test_objects_are_partitioned_by_domain_entity_and_business_date(
        self, dataset: Path
    ) -> None:
        store = MemoryStore()
        manifest = _ingest(store, dataset, "r1")
        date_fields = {
            "clearing_confirmations": "trade_date",
            "settlement_confirmations": "actual_settlement_date",
            "fee_records": "trade_date",
            "bank_statement_lines": "value_date",
            "fx_confirmations": "trade_date",
            "custody_snapshots": "snapshot_date",
            "price_feed": "price_date",
            "price_history": "price_date",
            "fx_rate_history": "rate_date",
        }
        events = {str(t) for t in EventType}
        for o in manifest.raw_objects():
            body = store.get(o.key)
            assert o.sha256 == hashlib.sha256(body).hexdigest()
            domain = o.key.split("/")[1]
            assert o.key == (
                f"raw/{domain}/{o.entity}/event_date={o.event_date}/part-{o.sha256}.jsonl"
            )
            for line in body.decode().split("\n")[:-1]:
                record = json.loads(line)
                if o.entity in events:
                    assert domain == (
                        "market_data"
                        if o.entity in {"price_updated", "fx_rate_published"}
                        else "brokerage"
                    )
                    day = business_date(datetime.fromisoformat(record["event_timestamp"]))
                else:
                    assert domain == EXTERNAL_DOMAINS[o.entity]
                    day = date.fromisoformat(record[date_fields[o.entity]])
                assert day == o.event_date
        expected = events | set(RECORD_MODELS) | set(HISTORY_MODELS)
        assert {o.entity for o in manifest.raw_objects()} == expected

    def test_a_replay_writes_no_data_objects(self, dataset: Path) -> None:
        store = MemoryStore()
        first = _ingest(store, dataset, "r1")
        keys = set(store.objects)
        second = _ingest(store, dataset, "r2")
        assert all(o.written for o in first.objects)
        assert not any(o.written for o in second.objects)
        assert [o.key for o in second.objects] == [o.key for o in first.objects]
        assert set(store.objects) - keys == {"audit/ingestion_manifests/r2.json"}

    def test_the_manifest_is_stored_and_a_run_id_is_used_once(self, dataset: Path) -> None:
        store = MemoryStore()
        manifest = _ingest(store, dataset, "r1")
        assert read_manifest(store, "r1") == manifest
        assert manifest.code_version == "test-sha"
        with pytest.raises(FileExistsError, match="r1"):
            _ingest(store, dataset, "r1")


def _broken_copy(dataset: Path, tmp_path: Path) -> Path:
    """The dataset with one of each kind of bad line appended."""
    root = tmp_path / "broken"
    shutil.copytree(dataset, root)
    events = root / "canonical" / "events.jsonl"
    lines = _lines(events)
    trade = json.loads(next(line for line in lines if '"trade_executed"' in line))
    deposit = next(line for line in lines if '"cash_deposited"' in line)
    withdrawal = next(line for line in lines if '"cash_withdrawn"' in line)
    saturday = dict(trade, event_id="evt_bad_saturday")
    saturday["payload"] = dict(trade["payload"], trade_date="2026-03-07")
    newer = json.loads(deposit) | {"event_id": "evt_bad_newer", "schema_version": 99}
    float_amount = json.loads(deposit) | {"event_id": "evt_bad_float"}
    float_amount["payload"] = dict(float_amount["payload"], amount=100.5)
    conflicting = json.loads(deposit)
    conflicting["payload"] = dict(conflicting["payload"], amount="1.00")
    extra = [
        "{not json",
        json.dumps({"event_type": "account_closed", "event_id": "evt_bad_type"}),
        json.dumps(newer),
        json.dumps(float_amount),
        json.dumps(saturday),
        withdrawal,  # an exact duplicate: counted, not quarantined
        json.dumps(conflicting),  # same event_id, different content
    ]
    events.write_text("\n".join([*lines, *extra]) + "\n", encoding="utf-8")
    bank = root / "external" / "bank_statement_lines.jsonl"
    bank_lines = _lines(bank)
    missing_field = json.loads(bank_lines[0])
    del missing_field["value_date"]
    missing_field["record_id"] = "bnk_bad"
    bank.write_text("\n".join([*bank_lines, json.dumps(missing_field)]) + "\n", encoding="utf-8")
    return root


class TestQuarantine:
    def test_each_bad_line_is_quarantined_with_its_reason(
        self, dataset: Path, tmp_path: Path
    ) -> None:
        root = _broken_copy(dataset, tmp_path)
        store = MemoryStore()
        manifest = _ingest(store, root, "r1")
        quarantined = [
            json.loads(line)
            for o in manifest.objects
            if o.area == "quarantine"
            for line in store.get(o.key).decode().split("\n")
            if line
        ]
        reasons = sorted(q["reason"] for q in quarantined)
        assert len(reasons) == 8  # 5 bad events + both conflicting copies + 1 bad bank line
        assert any(r.startswith("not JSON") for r in reasons)
        assert "unknown event_type 'account_closed'" in reasons
        assert "cash_deposited schema_version 99 is newer than supported (1)" in reasons
        assert any("payload.amount" in r for r in reasons)
        assert any("not a XTSE trading day" in r or "not a XNYS trading day" in r for r in reasons)
        assert sum("different content" in r for r in reasons) == 2
        assert any("value_date" in r for r in reasons)
        kinds = {o.key.split("/")[1]: o.rows for o in manifest.objects if o.area == "quarantine"}
        assert kinds == {"business_rule_failures": 3, "schema_errors": 5}
        events_file = next(f for f in manifest.files if f.path.endswith("events.jsonl"))
        assert (events_file.duplicates, events_file.quarantined) == (1, 7)
        raw_ids = {
            json.loads(line).get("event_id")
            for o in manifest.raw_objects()
            for line in store.get(o.key).decode().split("\n")
            if line
        }
        conflicting_id = json.loads(_lines(root / "canonical" / "events.jsonl")[-1])["event_id"]
        assert conflicting_id not in raw_ids, "no version of a conflicting ID reaches raw"

    def test_over_the_threshold_the_load_fails_and_writes_no_raw_data(
        self, dataset: Path, tmp_path: Path
    ) -> None:
        root = _broken_copy(dataset, tmp_path)
        store = MemoryStore()
        strict = QuarantinePolicy(
            version="strict", illustrative=True, max_quarantine_rate=Decimal(0)
        )
        with pytest.raises(
            QuarantineThresholdError, match=r"over the 0\.0000% threshold of policy strict"
        ):
            _ingest(store, root, "r1", strict)
        assert not any(k.startswith("raw/") for k in store.objects)
        manifest = read_manifest(store, "r1")
        assert manifest.status is RunStatus.FAILED
        assert sum(o.rows for o in manifest.objects if o.area == "quarantine") == 8

    def test_the_configured_policy_is_versioned_and_strict_enough_for_clean_data(
        self, dataset: Path
    ) -> None:
        policy = load_policy(CONFIG / "ingestion" / "quarantine_policy.yaml", QuarantinePolicy)
        assert policy.illustrative
        assert policy.max_quarantine_rate == Decimal("0.005")
        assert _ingest(MemoryStore(), dataset, "r1", policy).status is RunStatus.SUCCEEDED


def test_every_copy_of_a_conflicting_id_is_quarantined(dataset: Path, tmp_path: Path) -> None:
    # An identical copy doesn't vouch for one side of a conflict: all three go.
    root = tmp_path / "conflict"
    (root / "canonical").mkdir(parents=True)
    lines = _lines(dataset / "canonical" / "events.jsonl")
    deposit = next(line for line in lines if '"cash_deposited"' in line)
    other = json.loads(deposit)
    other["payload"] = dict(other["payload"], amount="1.00")
    (root / "canonical" / "events.jsonl").write_text(
        "\n".join([deposit, deposit, json.dumps(other)]) + "\n", encoding="utf-8"
    )
    manifest = _ingest(MemoryStore(), root, "r1")
    (events_file,) = manifest.files
    assert (events_file.accepted, events_file.duplicates, events_file.quarantined) == (0, 0, 3)
    assert manifest.raw_objects() == ()
