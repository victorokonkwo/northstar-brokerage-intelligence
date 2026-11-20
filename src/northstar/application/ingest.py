"""Use cases: ingest a dataset into the raw layer, and load a run into the warehouse.

``ingest_dataset`` validates the simulator's files into the object store's
raw layer and writes the run manifest (FR-M1-01 to FR-M1-03). ``load_run``
reads that run's raw objects back, posts the events with the v1 rules, and
loads events, journal lines, and external records into the warehouse in one
transaction. Replaying either step changes nothing.

A dataset goes into its own raw and ledger schemas (for example ``raw_dev``
and ``ledger_dev``), so its IDs never collide with the Sprint 1 slice's. dbt
builds over those schemas through the ``raw_schema`` / ``ledger_schema`` vars.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from opentelemetry import trace
from pydantic import TypeAdapter

from northstar.application.policies import load_policies
from northstar.application.reference_data import calendar_days
from northstar.domain.accounting import PostingEngine
from northstar.domain.events import Event, EventType
from northstar.infrastructure.config_loader import load_policy
from northstar.infrastructure.postgres import (
    ExternalRow,
    WarehouseSchemas,
    ensure_tables,
    load_calendar_days,
    load_events,
    load_external_records,
    load_journal,
)
from northstar.infrastructure.recon_store import ensure_recon_tables
from northstar.ingestion.pipeline import (
    IngestionManifest,
    ObjectStore,
    QuarantinePolicy,
    SourceFile,
    ingest,
    read_manifest,
)
from northstar.simulation.engine import LEDGER_CALENDAR_MARKET
from northstar.simulation.external import EXTERNAL_RECORD_SCHEMA_VERSION

_EVENTS = TypeAdapter(Event)
_EVENT_ENTITIES = frozenset(str(t) for t in EventType)


def ingest_dataset(
    store: ObjectStore,
    config_dir: Path,
    dataset_root: Path,
    run_id: str,
    code_version: str,
) -> IngestionManifest:
    """Ingest a generated dataset directory into the raw layer.

    Raises:
        QuarantineThresholdError: Too many lines were quarantined.
        FileNotFoundError: The dataset has no canonical events file.
        ConfigurationError: A policy file is missing or invalid.
    """
    with trace.get_tracer(__name__).start_as_current_span("northstar.ingest") as span:
        policy = load_policy(config_dir / "ingestion" / "quarantine_policy.yaml", QuarantinePolicy)
        calendars = load_policies(config_dir).calendars
        manifest = ingest(
            store,
            SourceFile.for_dataset(dataset_root),
            run_id,
            policy,
            calendars,
            code_version,
            datetime.now(UTC),
        )
        written = sum(o.written for o in manifest.objects)
        span.set_attributes({"northstar.run_id": run_id, "northstar.objects.written": written})
        logging.getLogger(__name__).info(
            "ingested run %s: %d objects (%d new), quarantine rate %s",
            run_id,
            len(manifest.objects),
            written,
            manifest.quarantine_rate,
            extra={"job": "ingest", "run_id": run_id},
        )
        return manifest


@dataclass(frozen=True, slots=True)
class RunLoad:
    """What a warehouse load read and inserted (0 inserted on an identical replay)."""

    events_read: int
    records_read: int
    batches_posted: int
    events_inserted: int
    lines_inserted: int
    records_inserted: int


def load_run(
    conn: psycopg.Connection[Any],
    store: ObjectStore,
    config_dir: Path,
    run_id: str,
    schemas: WarehouseSchemas,
) -> RunLoad:
    """Load a successful ingestion run's raw objects into the warehouse.

    Raises:
        ValueError: The run did not succeed, or a raw line no longer validates.
        LoadConflictError: Stored rows disagree with this input; nothing is committed.
    """
    manifest = read_manifest(store, run_id)
    if manifest.status != "SUCCEEDED":
        msg = f"run {run_id} did not succeed ({manifest.status}); nothing to load"
        raise ValueError(msg)
    events: list[Event] = []
    records: list[ExternalRow] = []
    for entry in manifest.raw_objects():
        lines = [line for line in store.get(entry.key).decode("utf-8").split("\n") if line]
        if entry.entity in _EVENT_ENTITIES:
            events.extend(_EVENTS.validate_json(line) for line in lines)
            continue
        if entry.event_date is None:  # pragma: no cover - raw objects always have a date
            msg = f"raw object {entry.key} has no event date"
            raise ValueError(msg)
        records.extend(
            ExternalRow(
                record_id=str(json.loads(line)["record_id"]),
                record_set=entry.entity,
                event_date=entry.event_date,
                schema_version=EXTERNAL_RECORD_SCHEMA_VERSION,
                payload=line,
            )
            for line in lines
        )
    policies = load_policies(config_dir)
    ordered = sorted(events, key=lambda e: (e.event_timestamp, e.event_id))
    batches = PostingEngine(policies.chart, policies.calendars[LEDGER_CALENDAR_MARKET]).post_events(
        ordered
    )
    with conn.transaction():
        ensure_tables(conn, schemas)
        inserted_events = load_events(conn, ordered, run_id, schemas)
        inserted_lines = load_journal(conn, batches, run_id, schemas)
        inserted_records = load_external_records(conn, records, run_id, schemas)
        load_calendar_days(conn, calendar_days(policies.calendars), run_id, schemas)
        # The recon tables exist from the first load, so dbt can build its
        # controls models before any reconciliation has run.
        ensure_recon_tables(conn, schemas.recon)
    return RunLoad(
        len(ordered), len(records), len(batches), inserted_events, inserted_lines, inserted_records
    )
