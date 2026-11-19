"""The Sprint 1 slice against the real warehouse, in throwaway schemas."""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql

from northstar.application.sprint1 import journal_fingerprint, load_slice, prepare_slice
from northstar.infrastructure.postgres import PostgresSettings, WarehouseSchemas

from factories import CONFIG

pytestmark = pytest.mark.integration

SCENARIO = CONFIG / "simulation" / "sprint1_scenario.yaml"


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[Any]]:
    with PostgresSettings.from_env().connect() as connection:
        yield connection


@pytest.fixture
def schemas(conn: psycopg.Connection[Any]) -> Iterator[WarehouseSchemas]:
    suffix = uuid.uuid4().hex[:12]
    names = WarehouseSchemas(raw=f"it_raw_{suffix}", ledger=f"it_ledger_{suffix}")
    yield names
    conn.rollback()
    with conn.cursor() as cur:
        for name in (names.raw, names.ledger):
            cur.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(name)))
    conn.commit()


def test_rerunning_the_slice_is_a_no_op_with_the_same_fingerprint(
    conn: psycopg.Connection[Any], schemas: WarehouseSchemas, tmp_path: Path
) -> None:
    first = load_slice(conn, prepare_slice(CONFIG, SCENARIO, 42, tmp_path), "run-1", schemas)
    fingerprint = journal_fingerprint(conn, schemas)
    second = load_slice(conn, prepare_slice(CONFIG, SCENARIO, 42, tmp_path), "run-2", schemas)
    assert (first.events_inserted, first.lines_inserted) == (6, 10)
    assert (second.events_inserted, second.lines_inserted) == (0, 0)
    assert journal_fingerprint(conn, schemas) == fingerprint


def test_fingerprint_changes_when_the_journal_changes(
    conn: psycopg.Connection[Any], schemas: WarehouseSchemas, tmp_path: Path
) -> None:
    load_slice(conn, prepare_slice(CONFIG, SCENARIO, 42, tmp_path), "run-1", schemas)
    before = journal_fingerprint(conn, schemas)
    # A direct UPDATE stands in for any tampering. It is possible here only
    # because append-only enforcement (INV-11) is not built yet.
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("update {} set amount = amount + 0.01 where line_number = 1").format(
                sql.Identifier(schemas.ledger, "journal_lines")
            )
        )
    assert journal_fingerprint(conn, schemas) != before
