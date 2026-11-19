"""Use case: load a generated dataset's events and posted journal into the warehouse.

A dataset goes into its own raw and ledger schemas (for example ``raw_dev`` and
``ledger_dev``), so its IDs never collide with the Sprint 1 slice's. dbt then
builds over those schemas through the ``raw_schema`` / ``ledger_schema`` vars.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

from northstar.application.policies import load_policies
from northstar.domain.accounting import PostingEngine
from northstar.infrastructure.event_files import read_events
from northstar.infrastructure.postgres import (
    WarehouseSchemas,
    ensure_tables,
    load_events,
    load_journal,
)
from northstar.simulation.engine import LEDGER_CALENDAR_MARKET


@dataclass(frozen=True, slots=True)
class DatasetLoad:
    """What a load read, posted, and inserted (0 inserted on an identical replay)."""

    events_read: int
    batches_posted: int
    events_inserted: int
    lines_inserted: int


def load_dataset(
    conn: psycopg.Connection[Any],
    config_dir: Path,
    events_path: Path,
    run_id: str,
    schemas: WarehouseSchemas,
) -> DatasetLoad:
    """Post the events with the v1 rules and load events and journal in one transaction.

    Raises:
        ValueError: The events file has an invalid line.
        LoadConflictError: Stored rows disagree with this input; nothing is committed.
    """
    policies = load_policies(config_dir)
    events = read_events(events_path)
    batches = PostingEngine(policies.chart, policies.calendars[LEDGER_CALENDAR_MARKET]).post_events(
        events
    )
    with conn.transaction():
        ensure_tables(conn, schemas)
        inserted_events = load_events(conn, events, run_id, schemas)
        inserted_lines = load_journal(conn, batches, run_id, schemas)
    return DatasetLoad(len(events), len(batches), inserted_events, inserted_lines)
