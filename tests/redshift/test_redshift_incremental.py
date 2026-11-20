"""On Redshift, incremental dbt builds equal full-refresh builds under late data (4F-b).

The same scenario as the Postgres integration test (``tests/incremental_scenario.py``),
run against the live workgroup in throwaway schemas. The admin creates the
append-only tables and grants the pipeline role, as ``redshift-bootstrap`` does.
Run by hand with ``make test-redshift``: it runs dbt eight times, so it uses
several minutes of compute.
"""

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from psycopg import sql

from northstar.infrastructure.dialect import RedshiftConnection
from northstar.infrastructure.postgres import WarehouseSchemas, ensure_tables
from northstar.infrastructure.recon_store import LOCK_TABLE, ensure_recon_tables
from northstar.infrastructure.redshift import RedshiftSettings, grant_pipeline

from incremental_scenario import BuildSchemas, assert_late_rounds_build_like_a_full_refresh

pytestmark = pytest.mark.redshift


@pytest.fixture
def conn() -> Iterator[RedshiftConnection]:
    with RedshiftSettings.from_env().connect() as connection:
        connection.autocommit = True  # each load commits before dbt reads it
        yield connection


@pytest.fixture
def schemas(conn: RedshiftConnection) -> Iterator[BuildSchemas]:
    suffix = uuid.uuid4().hex[:8]
    names = BuildSchemas(
        WarehouseSchemas(
            raw=f"t_raw_{suffix}", ledger=f"t_ledger_{suffix}", recon=f"t_recon_{suffix}"
        ),
        incremental=f"t_dbt_{suffix}",
        full=f"t_dbt_full_{suffix}",
    )
    secret = os.environ["NBI_RS_ADMIN_SECRET_ARN"]
    settings = RedshiftSettings.from_env()
    data = names.warehouse
    with settings.connect_admin(secret) as admin:
        ensure_tables(admin, data)
        ensure_recon_tables(admin, data.recon)
        grant_pipeline(
            admin,
            (data.raw, data.ledger, data.recon),
            [str(conn.info.user)],
            lock_tables=[(data.recon, LOCK_TABLE)],
        )
    yield names
    with settings.connect_admin(secret) as admin:
        for schema in (data.raw, data.ledger, data.recon, names.incremental, names.full):
            admin.execute(
                sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema))
            )


def test_late_records_build_incrementally_to_the_full_refresh_result(
    conn: RedshiftConnection, schemas: BuildSchemas, tmp_path: Path
) -> None:
    assert_late_rounds_build_like_a_full_refresh(conn, "redshift", schemas, tmp_path)
