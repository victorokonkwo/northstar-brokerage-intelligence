"""Sprint 1 vertical slice: deposit -> trade -> ledger -> warehouse.

``prepare_slice`` simulates the scenario, writes the canonical events file, and
posts the journal (no database). ``load_slice`` loads both into the warehouse
in one transaction. ``run_slice`` does both. dbt then builds and tests the
models (``make e2e``).

Each step emits an OpenTelemetry span, counters for the rows it produced, and
a structured log line (ADR 008). It uses only the OpenTelemetry API; the
entry point decides whether anything is exported.
"""

import hashlib
import logging
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

import psycopg
from opentelemetry import metrics, trace
from opentelemetry.util.types import AttributeValue
from psycopg import sql

from northstar.domain.accounting import ChartOfAccounts, JournalBatch, PostingEngine
from northstar.domain.calendar import MarketCalendar
from northstar.domain.events import Event
from northstar.domain.settlement import SettlementRules
from northstar.domain.trading import FeeSchedule
from northstar.infrastructure.config_loader import load_policy
from northstar.infrastructure.event_files import write_events
from northstar.infrastructure.postgres import (
    WarehouseSchemas,
    ensure_tables,
    load_events,
    load_journal,
)
from northstar.simulation.scenario import Policies, Scenario, generate_events

LEDGER_CALENDAR_MARKET: Final = "XTSE"
"""The ledger's business calendar is the XTSE CAD settlement calendar (domain model §5)."""

_INSTRUMENTATION: Final = "northstar.application.sprint1"


@dataclass(frozen=True, slots=True)
class PreparedSlice:
    """The simulated events, where they were written, and the posted journal."""

    scenario_id: str
    events: tuple[Event, ...]
    events_path: Path
    events_sha256: str
    batches: tuple[JournalBatch, ...]


@dataclass(frozen=True, slots=True)
class LoadCounts:
    """How many rows a load inserted (0 on an identical replay)."""

    events_inserted: int
    lines_inserted: int


def load_policies(config_dir: Path) -> tuple[Policies, ChartOfAccounts]:
    """Load every policy the slice needs from ``config_dir``.

    Raises:
        ConfigurationError: A policy file is missing or invalid.
    """
    calendar = load_policy(config_dir / "calendars" / "xtse.yaml", MarketCalendar)
    policies = Policies(
        calendars={calendar.market: calendar},
        settlement_rules=load_policy(
            config_dir / "brokerage" / "settlement_rules.yaml", SettlementRules
        ),
        fee_schedule=load_policy(config_dir / "brokerage" / "fee_schedule.yaml", FeeSchedule),
    )
    chart = load_policy(config_dir / "accounting" / "chart_of_accounts.yaml", ChartOfAccounts)
    return policies, chart


def prepare_slice(config_dir: Path, scenario_path: Path, seed: int, out_dir: Path) -> PreparedSlice:
    """Simulate the scenario, write its events file, and post the journal.

    The events file is ``<out_dir>/<scenario_id>/seed=<seed>/events.jsonl``.
    The same inputs always produce the same bytes (FR-SIM-01).

    Raises:
        ConfigurationError: A policy or scenario file is missing or invalid.
        ScenarioError, NotATradingDayError, PolicyNotFoundError: The scenario
            cannot be simulated under the policies.
    """
    tracer = trace.get_tracer(_INSTRUMENTATION)
    with tracer.start_as_current_span("northstar.prepare_slice") as span:
        policies, chart = load_policies(config_dir)
        scenario = load_policy(scenario_path, Scenario)
        events = generate_events(scenario, seed, policies)
        events_path = out_dir / scenario.scenario_id / f"seed={seed}" / "events.jsonl"
        write_events(events_path, events)
        engine = PostingEngine(chart, policies.calendars[LEDGER_CALENDAR_MARKET])
        prepared = PreparedSlice(
            scenario_id=scenario.scenario_id,
            events=events,
            events_path=events_path,
            events_sha256=hashlib.sha256(events_path.read_bytes()).hexdigest(),
            batches=engine.post_events(events),
        )
        attributes: dict[str, AttributeValue] = {
            "northstar.scenario_id": prepared.scenario_id,
            "northstar.seed": seed,
        }
        span.set_attributes(
            attributes
            | {
                "northstar.events.count": len(prepared.events),
                "northstar.journal_batches.count": len(prepared.batches),
                "northstar.events.sha256": prepared.events_sha256,
            }
        )
        meter = metrics.get_meter(_INSTRUMENTATION)
        meter.create_counter(
            "northstar.events.generated", unit="{event}", description="Canonical events simulated"
        ).add(len(prepared.events), attributes)
        meter.create_counter(
            "northstar.journal_batches.posted", unit="{batch}", description="Journal batches posted"
        ).add(len(prepared.batches), attributes)
        logging.getLogger(__name__).info(
            "prepared slice: %d events, %d journal batches",
            len(prepared.events),
            len(prepared.batches),
            extra={"job": "run-slice", "scenario_id": prepared.scenario_id, "seed": seed},
        )
        return prepared


def load_slice(
    conn: psycopg.Connection[Any],
    prepared: PreparedSlice,
    run_id: str,
    schemas: WarehouseSchemas,
) -> LoadCounts:
    """Load the events and journal lines in one transaction.

    Raises:
        LoadConflictError: Stored rows disagree with this input. Nothing is
            committed.
    """
    tracer = trace.get_tracer(_INSTRUMENTATION)
    with tracer.start_as_current_span("northstar.load_slice") as span:
        with conn.transaction():
            ensure_tables(conn, schemas)
            events_inserted = load_events(conn, prepared.events, run_id, schemas)
            lines_inserted = load_journal(conn, prepared.batches, run_id, schemas)
        span.set_attributes(
            {
                "northstar.run_id": run_id,
                "northstar.events.inserted": events_inserted,
                "northstar.journal_lines.inserted": lines_inserted,
            }
        )
        meter = metrics.get_meter(_INSTRUMENTATION)
        attributes: dict[str, AttributeValue] = {"northstar.scenario_id": prepared.scenario_id}
        meter.create_counter(
            "northstar.events.loaded", unit="{event}", description="New events inserted"
        ).add(events_inserted, attributes)
        meter.create_counter(
            "northstar.journal_lines.loaded",
            unit="{line}",
            description="New journal lines inserted",
        ).add(lines_inserted, attributes)
        logging.getLogger(__name__).info(
            "loaded slice: %d new events, %d new journal lines",
            events_inserted,
            lines_inserted,
            extra={"job": "run-slice", "run_id": run_id, "scenario_id": prepared.scenario_id},
        )
    return LoadCounts(events_inserted, lines_inserted)


def journal_fingerprint(conn: psycopg.Connection[Any], schemas: WarehouseSchemas) -> str:
    """Return a SHA-256 over every stored journal line's business content.

    Audit columns (run ID, ingestion time) are excluded, so two runs over the
    same inputs give the same fingerprint (INV-09 for this slice).
    """
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "select journal_line_id, journal_batch_id, line_number, source_event_id, "
                "posting_rule_id, posting_rule_version, business_date, posting_date, "
                "gl_account, client_account_id, entry_side, amount, currency "
                "from {} order by journal_line_id"
            ).format(sql.Identifier(schemas.ledger, "journal_lines"))
        )
        digest = hashlib.sha256()
        for row in cur:
            digest.update("|".join("" if v is None else str(v) for v in row).encode())
            digest.update(b"\n")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class ControlRow:
    """One row of ``mart_ledger_balance_controls`` (read, never recomputed)."""

    business_date: str
    currency: str
    batch_count: int
    line_count: int
    total_debit: Decimal
    total_credit: Decimal
    unbalanced_batch_count: int


def read_ledger_controls(conn: psycopg.Connection[Any], dbt_schema: str) -> list[ControlRow]:
    """Read the KPI-01 control mart that dbt built (ADR 007: no recomputation here)."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "select business_date, currency, batch_count, line_count, total_debit, "
                "total_credit, unbalanced_batch_count from {} order by business_date, currency"
            ).format(sql.Identifier(dbt_schema, "mart_ledger_balance_controls"))
        )
        return [
            ControlRow(str(d), c, int(b), int(n), Decimal(td), Decimal(tc), int(u))
            for d, c, b, n, td, tc, u in cur.fetchall()
        ]
