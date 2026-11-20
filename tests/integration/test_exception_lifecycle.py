"""The exception lifecycle against Postgres: rules, manual changes, and the audit trail."""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg import sql

from northstar.application.lifecycle import apply_rules, transition_exception
from northstar.common.money import Currency
from northstar.domain.lifecycle import IllegalTransitionError, Resolution, Status
from northstar.domain.reconciliation import (
    ExceptionType,
    ReconciliationPolicy,
    ReconException,
    Severity,
)
from northstar.infrastructure.config_loader import load_policy
from northstar.infrastructure.postgres import PostgresSettings
from northstar.infrastructure.recon_store import (
    RunRecord,
    current_states,
    ensure_recon_tables,
    history,
    store_run,
)

from factories import CONFIG, xtse

pytestmark = pytest.mark.integration

POLICY = load_policy(CONFIG / "reconciliation" / "policy.yaml", ReconciliationPolicy)
NOW = datetime(2026, 3, 10, 6, 0, tzinfo=UTC)


def _exception(n: int, kind: ExceptionType, severity: Severity, day: date) -> ReconException:
    return ReconException(
        exception_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"test-{n}")),
        reconciliation="cash",
        exception_type=kind,
        severity=severity,
        reference=f"ref_{n}",
        business_date=day,
        impact_amount=Decimal("100.00"),
        impact_currency=Currency.CAD,
        impact_basis="amount",
        impact_cad=Decimal("100.00"),
        fx_rate_date=None,
        fx_policy_version=None,
        external_record_ids=(),
        detail="test",
        policy_version="2026.1",
    )


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[Any]]:
    with PostgresSettings.from_env().connect() as connection:
        connection.autocommit = True
        yield connection


@pytest.fixture
def schema(conn: psycopg.Connection[Any]) -> Iterator[str]:
    name = f"it_recon_{uuid.uuid4().hex[:12]}"
    ensure_recon_tables(conn, name)
    yield name
    with conn.cursor() as cur:
        cur.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(name)))


def _store(
    conn: psycopg.Connection[Any], schema: str, exceptions: tuple[ReconException, ...], run: str
) -> None:
    store_run(
        conn,
        schema,
        RunRecord(run, date(2026, 3, 10), "src", "2026.1", "test", NOW),
        (),
        exceptions,
    )


def test_rules_open_correct_resolve_and_escalate(
    conn: psycopg.Connection[Any], schema: str
) -> None:
    recent = _exception(1, ExceptionType.FEE_MISMATCH, Severity.MEDIUM, date(2026, 3, 9))
    old = _exception(2, ExceptionType.CASH_BREAK, Severity.CRITICAL, date(2026, 3, 2))
    late = _exception(3, ExceptionType.SETTLEMENT_LATE, Severity.MEDIUM, date(2026, 3, 9))
    _store(conn, schema, (recent, old, late), "run-1")
    found = frozenset(x.exception_id for x in (recent, old, late))
    first = apply_rules(
        conn, schema, found, frozenset({"ref_3"}), POLICY, xtse(), date(2026, 3, 10), "run-1", NOW
    )
    assert (first.opened, first.auto_resolved, first.escalated, first.source_corrected) == (
        3,
        1,
        1,
        0,
    )
    states = current_states(conn, schema)
    assert {states[x.exception_id].status for x in (recent, old, late)} == {
        "OPEN",
        "ESCALATED",
        "RESOLVED",
    }
    # A rerun with the same findings changes nothing.
    again = apply_rules(
        conn, schema, found, frozenset({"ref_3"}), POLICY, xtse(), date(2026, 3, 10), "run-2", NOW
    )
    assert (again.opened, again.auto_resolved, again.escalated, again.source_corrected) == (
        0,
        0,
        0,
        0,
    )
    # A run that no longer finds the recent break resolves it as source-corrected.
    later = apply_rules(
        conn,
        schema,
        found - {recent.exception_id},
        frozenset({"ref_3"}),
        POLICY,
        xtse(),
        date(2026, 3, 10),
        "run-3",
        NOW,
    )
    assert later.source_corrected == 1
    (last,) = history(conn, schema, recent.exception_id)[-1:]
    assert (last.to_status, last.resolution_type, last.actor) == (
        "RESOLVED",
        "SOURCE_CORRECTED",
        "rule:source_corrected@1",
    )


def test_manual_changes_follow_the_lifecycle(conn: psycopg.Connection[Any], schema: str) -> None:
    x = _exception(4, ExceptionType.FEE_MISMATCH, Severity.LOW, date(2026, 3, 10))
    _store(conn, schema, (x,), "run-1")
    apply_rules(
        conn,
        schema,
        frozenset({x.exception_id}),
        frozenset(),
        POLICY,
        xtse(),
        date(2026, 3, 10),
        "run-1",
        NOW,
    )
    with pytest.raises(IllegalTransitionError, match="only a rule may resolve an OPEN"):
        transition_exception(
            conn,
            schema,
            x.exception_id,
            Status.RESOLVED,
            "user:test",
            "done",
            Resolution.MANUAL_OVERRIDE,
            NOW,
        )
    transition_exception(
        conn, schema, x.exception_id, Status.INVESTIGATING, "user:test", "looking", None, NOW
    )
    transition_exception(
        conn,
        schema,
        x.exception_id,
        Status.RESOLVED,
        "user:test",
        "vendor fixed the fee",
        Resolution.MANUAL_OVERRIDE,
        NOW,
    )
    assert [t.to_status for t in history(conn, schema, x.exception_id)] == [
        "OPEN",
        "INVESTIGATING",
        "RESOLVED",
    ]
    with pytest.raises(LookupError):
        transition_exception(
            conn, schema, str(uuid.uuid4()), Status.INVESTIGATING, "user:test", "x", None, NOW
        )


def test_the_database_refuses_unproven_resolutions_and_edits(
    conn: psycopg.Connection[Any], schema: str
) -> None:
    x = _exception(5, ExceptionType.FEE_MISMATCH, Severity.LOW, date(2026, 3, 10))
    _store(conn, schema, (x,), "run-1")
    table = sql.Identifier(schema, "exception_transitions")
    insert = sql.SQL(
        "insert into {} (transition_id, exception_id, sequence, from_status, to_status, actor, "
        "reason, resolution_type, changed_at) "
        "values (%s, %s, 1, 'INVESTIGATING', 'RESOLVED', %s, 'r', %s, now())"
    ).format(table)
    for actor, resolution in (
        ("user:test", None),
        ("rule:x@1", "MANUAL_OVERRIDE"),
        ("user:test", "AUTO_RULE"),
    ):
        with pytest.raises(psycopg.errors.CheckViolation), conn.cursor() as cur:
            cur.execute(insert, (uuid.uuid4(), x.exception_id, actor, resolution))
    apply_rules(
        conn,
        schema,
        frozenset({x.exception_id}),
        frozenset(),
        POLICY,
        xtse(),
        date(2026, 3, 10),
        "run-1",
        NOW,
    )
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"), conn.cursor() as cur:
        cur.execute(sql.SQL("update {} set reason = 'edited'").format(table))
