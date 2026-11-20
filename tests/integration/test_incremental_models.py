"""Incremental dbt builds equal full-refresh builds under late data (roadmap 4F-b).

The scenario and its assertions are in ``tests/incremental_scenario.py``.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql

from northstar.infrastructure.postgres import PostgresSettings, WarehouseSchemas

from incremental_scenario import BuildSchemas, assert_late_rounds_build_like_a_full_refresh

pytestmark = pytest.mark.integration


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[Any]]:
    with PostgresSettings.from_env().connect() as connection:
        connection.autocommit = True  # each load commits before dbt reads it
        yield connection


@pytest.fixture
def schemas(conn: psycopg.Connection[Any]) -> Iterator[BuildSchemas]:
    suffix = uuid.uuid4().hex[:10]
    names = BuildSchemas(
        WarehouseSchemas(
            raw=f"it_raw_{suffix}", ledger=f"it_ledger_{suffix}", recon=f"it_recon_{suffix}"
        ),
        incremental=f"it_dbt_{suffix}",
        full=f"it_dbt_full_{suffix}",
    )
    yield names
    for name in (
        names.warehouse.raw,
        names.warehouse.ledger,
        names.warehouse.recon,
        names.incremental,
        names.full,
    ):
        conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(name)))


def test_late_records_build_incrementally_to_the_full_refresh_result(
    conn: psycopg.Connection[Any], schemas: BuildSchemas, tmp_path: Path
) -> None:
    assert_late_rounds_build_like_a_full_refresh(conn, "postgres", schemas, tmp_path)
