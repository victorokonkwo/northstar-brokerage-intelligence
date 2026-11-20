"""Versioned publication (ADR 006, FR-M1-06, INV-09) against throwaway schemas.

The source schema stands in for dbt's output: the four gate marts with a
business date, a value, and the lineage-only ``_dbt_invocation_id``, plus the
controls mart and reporting dates. No dbt run is needed.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from psycopg import sql

from northstar.application.lifecycle import apply_rules, transition_exception
from northstar.application.publish import Gate, Outcome, Stream, publish_date
from northstar.common.exceptions import PublicationBlockedError
from northstar.common.money import Currency
from northstar.domain.lifecycle import Resolution, Status
from northstar.domain.reconciliation import (
    ExceptionType,
    ReconciliationPolicy,
    ReconException,
    Severity,
)
from northstar.infrastructure.config_loader import load_policy
from northstar.infrastructure.postgres import PostgresSettings
from northstar.infrastructure.recon_store import RunRecord, ensure_recon_tables, store_run
from northstar.infrastructure.snapshots import (
    CONTROLS_MARTS,
    PUBLISHED_MARTS,
    history,
    read_as_published,
)

from factories import CONFIG, xtse

POLICY = load_policy(CONFIG / "reconciliation" / "policy.yaml", ReconciliationPolicy)

pytestmark = pytest.mark.integration

D = date(2026, 3, 2)
T0 = datetime(2026, 3, 3, 6, 0, tzinfo=UTC)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[Any]]:
    with PostgresSettings.from_env().connect() as connection:
        connection.autocommit = True
        yield connection


@pytest.fixture
def schemas(conn: psycopg.Connection[Any]) -> Iterator[tuple[str, str]]:
    suffix = uuid.uuid4().hex[:12]
    source, published = f"it_dbt_{suffix}", f"it_pub_{suffix}"
    with conn.cursor() as cur:
        cur.execute(sql.SQL("create schema {}").format(sql.Identifier(source)))
        for mart in (*PUBLISHED_MARTS, *CONTROLS_MARTS):
            cur.execute(
                sql.SQL(
                    "create table {} (business_date date not null, account_id text, "
                    "amount numeric(18, 2), _dbt_invocation_id text not null)"
                ).format(sql.Identifier(source, mart))
            )
            _insert(cur, source, mart, "acc_1", "100.00", "dbt-run-1")
        cur.execute(
            sql.SQL("create table {} (business_date date, unbalanced_batch_count bigint)").format(
                sql.Identifier(source, "mart_ledger_balance_controls")
            )
        )
        cur.execute(
            sql.SQL("insert into {} values (%s, 0)").format(
                sql.Identifier(source, "mart_ledger_balance_controls")
            ),
            (D,),
        )
        cur.execute(
            sql.SQL("create table {} (business_date date)").format(
                sql.Identifier(source, "int_reporting_dates")
            )
        )
        cur.execute(
            sql.SQL("insert into {} values (%s)").format(
                sql.Identifier(source, "int_reporting_dates")
            ),
            (D,),
        )
    ensure_recon_tables(conn, f"{source}_recon")
    yield source, published
    with conn.cursor() as cur:
        for name in (source, published, f"{source}_recon", f"{published}_controls"):
            cur.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(name)))


def _insert(
    cur: psycopg.Cursor[Any], source: str, mart: str, account: str, amount: str, run: str
) -> None:
    cur.execute(
        sql.SQL("insert into {} values (%s, %s, %s, %s)").format(sql.Identifier(source, mart)),
        (D, account, amount, run),
    )


def _gate(schemas: tuple[str, str]) -> Gate:
    return Gate(f"{schemas[0]}_recon", POLICY)


def _publish(
    conn: psycopg.Connection[Any], schemas: tuple[str, str], at: datetime, note: str | None = None
) -> Any:
    source, published = schemas
    return publish_date(
        conn, source, published, D, "run", "test-sha", at, note, Stream.FINANCIAL, _gate(schemas)
    )


def test_first_publication_then_identical_reruns_change_nothing(
    conn: psycopg.Connection[Any], schemas: tuple[str, str]
) -> None:
    first = _publish(conn, schemas, T0)
    assert (first.outcome, first.snapshot_version, first.restatement_reason) == (
        Outcome.PUBLISHED,
        1,
        None,
    )
    # A new dbt run changes only the lineage-only column: not new content (INV-09).
    source, published = schemas
    with conn.cursor() as cur:
        for mart in PUBLISHED_MARTS:
            cur.execute(
                sql.SQL("update {} set _dbt_invocation_id = 'dbt-run-2'").format(
                    sql.Identifier(source, mart)
                )
            )
    again = _publish(conn, schemas, T0 + timedelta(hours=1))
    assert (again.outcome, again.snapshot_version) == (Outcome.UNCHANGED, 1)
    assert again.content_sha256 == first.content_sha256
    assert [v.status for v in history(conn, published, D)] == ["PUBLISHED"]


def test_a_late_record_restates_the_date_and_keeps_the_old_version(
    conn: psycopg.Connection[Any], schemas: tuple[str, str]
) -> None:
    source, published = schemas
    _publish(conn, schemas, T0)
    with conn.cursor() as cur:
        _insert(cur, source, "mart_daily_client_cash", "acc_2", "25.00", "dbt-run-2")
    t1 = T0 + timedelta(days=1)
    restated = _publish(conn, schemas, t1, note="late deposit")
    assert (restated.outcome, restated.snapshot_version, restated.superseded_version) == (
        Outcome.PUBLISHED,
        2,
        1,
    )
    assert (
        restated.restatement_reason == "restates v1: mart_daily_client_cash changed; late deposit"
    )
    v1, v2 = history(conn, published, D)
    assert (v1.status, v1.superseded_at, v2.status, v2.published_at) == (
        "SUPERSEDED",
        t1,
        "PUBLISHED",
        t1,
    )
    # Point in time: before the restatement v1 was the published truth, after it v2.
    before = read_as_published(
        conn, published, "mart_daily_client_cash", D, t1 - timedelta(seconds=1)
    )
    after = read_as_published(conn, published, "mart_daily_client_cash", D, t1)
    assert before[0] == 1
    assert len(before[1]) == 1
    assert after[0] == 2
    assert len(after[1]) == 2
    assert read_as_published(
        conn, published, "mart_daily_client_cash", D, T0 - timedelta(seconds=1)
    ) == (None, [])
    # The superseded rows are still there, unchanged.
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("select count(*) from {} where snapshot_version = 1").format(
                sql.Identifier(published, "mart_daily_client_cash")
            )
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 1


def test_a_failing_gate_leaves_a_draft_and_the_last_publication_in_place(
    conn: psycopg.Connection[Any], schemas: tuple[str, str]
) -> None:
    source, published = schemas
    _publish(conn, schemas, T0)
    with conn.cursor() as cur:
        _insert(cur, source, "mart_daily_finance", "acc_9", "1.00", "dbt-run-2")
        cur.execute(
            sql.SQL("insert into {} values (%s, 1)").format(
                sql.Identifier(source, "mart_ledger_balance_controls")
            ),
            (D,),
        )
    with pytest.raises(PublicationBlockedError, match="KPI-01 is 1"):
        _publish(conn, schemas, T0 + timedelta(days=1))
    assert [v.status for v in history(conn, published, D)] == ["PUBLISHED", "DRAFT"]
    # Once fixed, the next version replaces the draft and supersedes v1.
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("delete from {} where unbalanced_batch_count = 1").format(
                sql.Identifier(source, "mart_ledger_balance_controls")
            )
        )
    fixed = _publish(conn, schemas, T0 + timedelta(days=2))
    assert (fixed.snapshot_version, fixed.superseded_version) == (3, 1)
    # Compared with the draft it replaces, only the gate result differs.
    assert fixed.restatement_reason == "replaces draft v2: KPI-01 changed"
    assert [v.status for v in history(conn, published, D)] == ["SUPERSEDED", "DRAFT", "PUBLISHED"]


def test_published_snapshots_are_append_only(
    conn: psycopg.Connection[Any], schemas: tuple[str, str]
) -> None:
    _, published = schemas
    _publish(conn, schemas, T0)
    for statement in (
        "update {t} set amount = 0",
        "delete from {t}",
    ):
        with (
            pytest.raises(psycopg.errors.RaiseException, match="append-only"),
            conn.cursor() as cur,
        ):
            cur.execute(
                sql.SQL(statement).format(t=sql.Identifier(published, "mart_daily_client_cash"))
            )
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"), conn.cursor() as cur:
        cur.execute(
            sql.SQL("update {} set status = 'PUBLISHED'").format(
                sql.Identifier(published, "snapshot_status_log")
            )
        )


def test_only_reporting_dates_can_be_published(
    conn: psycopg.Connection[Any], schemas: tuple[str, str]
) -> None:
    source, published = schemas
    with pytest.raises(ValueError, match="not a reporting date"):
        publish_date(
            conn,
            source,
            published,
            D + timedelta(days=5),
            "run",
            "sha",
            T0,
            stream=Stream.FINANCIAL,
            gate=_gate(schemas),
        )


def _blocking(conn: psycopg.Connection[Any], schemas: tuple[str, str], severity: Severity) -> str:
    recon = f"{schemas[0]}_recon"
    x = ReconException(
        exception_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"gate-{severity}")),
        reconciliation="cash",
        exception_type=ExceptionType.FEE_MISMATCH,
        severity=severity,
        reference="ref",
        business_date=D,
        impact_amount=Decimal("1.00"),
        impact_currency=Currency.CAD,
        impact_basis="amount",
        impact_cad=Decimal("1.00"),
        fx_rate_date=None,
        fx_policy_version=None,
        external_record_ids=(),
        detail="t",
        policy_version="2026.1",
    )
    with conn.transaction():
        store_run(conn, recon, RunRecord("r", D, "s", "2026.1", "t", T0), (), (x,))
        apply_rules(
            conn, recon, frozenset({x.exception_id}), frozenset(), POLICY, xtse(), D, "r", T0
        )
    return x.exception_id


def test_an_open_blocking_exception_holds_publication_until_resolved(
    conn: psycopg.Connection[Any], schemas: tuple[str, str]
) -> None:
    exception_id = _blocking(conn, schemas, Severity.HIGH)
    with pytest.raises(PublicationBlockedError, match="1 open blocking exception"):
        _publish(conn, schemas, T0)
    _, published = schemas
    assert [v.status for v in history(conn, published, D)] == ["DRAFT"]
    # Resolving it (a person investigates, then overrides with a reason) releases
    # the same version: the content did not change, so there is no v2.
    recon = f"{schemas[0]}_recon"
    transition_exception(
        conn, recon, exception_id, Status.INVESTIGATING, "user:test", "looking", None, T0
    )
    transition_exception(
        conn,
        recon,
        exception_id,
        Status.RESOLVED,
        "user:test",
        "fee confirmed",
        Resolution.MANUAL_OVERRIDE,
        T0,
    )
    released = _publish(conn, schemas, T0 + timedelta(hours=1))
    assert (released.outcome, released.snapshot_version) == (Outcome.PUBLISHED, 1)
    assert [v.status for v in history(conn, published, D)] == ["PUBLISHED"]


def test_low_and_medium_exceptions_do_not_block(
    conn: psycopg.Connection[Any], schemas: tuple[str, str]
) -> None:
    _blocking(conn, schemas, Severity.LOW)
    assert _publish(conn, schemas, T0).outcome is Outcome.PUBLISHED


def test_the_controls_stream_ignores_exceptions_and_the_financial_one_needs_a_gate(
    conn: psycopg.Connection[Any], schemas: tuple[str, str]
) -> None:
    _blocking(conn, schemas, Severity.CRITICAL)
    source, published = schemas
    controls = publish_date(
        conn, source, f"{published}_controls", D, "run", "sha", T0, stream=Stream.CONTROLS
    )
    assert controls.outcome is Outcome.PUBLISHED
    with pytest.raises(ValueError, match="needs a gate"):
        publish_date(conn, source, published, D, "run", "sha", T0)
