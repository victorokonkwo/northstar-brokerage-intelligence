"""Loads into the real warehouse Postgres (needs `make up` and the NBI_PG_* variables).

Each test module uses throwaway schemas, so it never touches the raw and
ledger tables that the pipeline writes.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg import sql

from northstar.common.money import Currency
from northstar.domain.events import CashDeposited, Event
from northstar.infrastructure.config_loader import load_policy
from northstar.infrastructure.postgres import (
    LoadConflictError,
    PostgresSettings,
    WarehouseSchemas,
    ensure_tables,
    load_events,
    load_journal,
)
from northstar.simulation.scenario import Scenario, generate_events

from factories import CONFIG, engine, policies

pytestmark = pytest.mark.integration


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[Any]]:
    with PostgresSettings.from_env().connect() as connection:
        yield connection


@pytest.fixture
def schemas(conn: psycopg.Connection[Any]) -> Iterator[WarehouseSchemas]:
    suffix = uuid.uuid4().hex[:12]
    names = WarehouseSchemas(raw=f"it_raw_{suffix}", ledger=f"it_ledger_{suffix}")
    ensure_tables(conn, names)
    conn.commit()
    yield names
    conn.rollback()
    with conn.cursor() as cur:
        for name in (names.raw, names.ledger):
            cur.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(name)))
    conn.commit()


@pytest.fixture(scope="module")
def events() -> tuple[Event, ...]:
    scenario = load_policy(CONFIG / "simulation" / "sprint1_scenario.yaml", Scenario)
    return generate_events(scenario, 42, policies())


def _rows(conn: psycopg.Connection[Any], schemas: WarehouseSchemas) -> tuple[list[Any], list[Any]]:
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("select event_id, event_type, payload from {} order by event_id").format(
                sql.Identifier(schemas.raw, "brokerage_events")
            )
        )
        events = cur.fetchall()
        cur.execute(
            sql.SQL(
                "select journal_line_id, gl_account, entry_side, amount, currency, posting_date "
                "from {} order by journal_line_id"
            ).format(sql.Identifier(schemas.ledger, "journal_lines"))
        )
        return events, cur.fetchall()


def test_replaying_a_load_inserts_nothing_and_changes_nothing(
    conn: psycopg.Connection[Any], schemas: WarehouseSchemas, events: tuple[Event, ...]
) -> None:
    batches = engine().post_events(events)
    assert load_events(conn, events, "run-1", schemas) == 7
    assert load_journal(conn, batches, "run-1", schemas) == 14  # includes 2 clearing-fee pairs
    conn.commit()
    first = _rows(conn, schemas)

    assert load_events(conn, events, "run-2", schemas) == 0
    assert load_journal(conn, engine().post_events(events), "run-2", schemas) == 0
    conn.commit()
    assert _rows(conn, schemas) == first


def test_amounts_round_trip_exactly_as_numeric(
    conn: psycopg.Connection[Any], schemas: WarehouseSchemas, events: tuple[Event, ...]
) -> None:
    load_journal(conn, engine().post_events(events), "run-1", schemas)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "select currency, sum(amount) filter (where entry_side = 'DEBIT'), "
                "sum(amount) filter (where entry_side = 'CREDIT') from {} group by currency"
            ).format(sql.Identifier(schemas.ledger, "journal_lines"))
        )
        assert [(c.strip(), str(d), str(cr)) for c, d, cr in cur.fetchall()] == [
            ("CAD", "35488.10", "35488.10")
        ]


def test_a_replay_with_different_content_is_refused(
    conn: psycopg.Connection[Any], schemas: WarehouseSchemas, events: tuple[Event, ...]
) -> None:
    load_events(conn, events, "run-1", schemas)
    conn.commit()
    deposit = next(e for e in events if isinstance(e, CashDeposited))
    tampered = deposit.model_copy(
        update={"payload": deposit.payload.model_copy(update={"currency": Currency.USD})}
    )
    with pytest.raises(LoadConflictError, match="stored rows differ"):
        load_events(conn, [tampered], "run-2", schemas)


def test_conflicting_duplicates_within_one_input_are_refused(
    conn: psycopg.Connection[Any], schemas: WarehouseSchemas, events: tuple[Event, ...]
) -> None:
    deposit = next(e for e in events if isinstance(e, CashDeposited))
    tampered = deposit.model_copy(update={"correlation_id": "something-else"})
    with pytest.raises(LoadConflictError, match="repeats event_id"):
        load_events(conn, [deposit, tampered], "run-1", schemas)


def test_database_constraints_reject_a_non_positive_amount(
    conn: psycopg.Connection[Any], schemas: WarehouseSchemas
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "insert into {} values (gen_random_uuid(), gen_random_uuid(), 1, 'e', 't', 'r', 1, "
                "'1', '1', current_date, current_date, '1000', null, 'DEBIT', 0, 'CAD', 'run')"
            ).format(sql.Identifier(schemas.ledger, "journal_lines"))
        )
