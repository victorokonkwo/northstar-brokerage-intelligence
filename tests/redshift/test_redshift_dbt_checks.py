"""On Redshift, the dbt tests catch every row the Postgres constraints would refuse.

Redshift has no CHECK constraints and does not enforce UNIQUE, so rows that
Postgres refuses can be stored there. This inserts one row per broken rule
into throwaway schemas, builds the staging views over them, and checks that
each dbt test returns exactly those rows (owner decision 2026-11-20). Run by
hand with ``make test-redshift``.
"""

import os
import re
import subprocess
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from psycopg import sql

from northstar.infrastructure.dialect import RedshiftConnection
from northstar.infrastructure.postgres import WarehouseSchemas, ensure_tables
from northstar.infrastructure.recon_store import LOCK_TABLE, ensure_recon_tables
from northstar.infrastructure.redshift import RedshiftSettings, grant_pipeline

pytestmark = pytest.mark.redshift

DBT = Path(__file__).parents[2] / "warehouse" / "dbt"
NOW = datetime(2026, 3, 2, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def schemas() -> Iterator[WarehouseSchemas]:
    settings = RedshiftSettings.from_env()
    suffix = uuid.uuid4().hex[:8]
    names = WarehouseSchemas(
        raw=f"t_raw_{suffix}", ledger=f"t_ledger_{suffix}", recon=f"t_recon_{suffix}"
    )
    with settings.connect() as me:
        user = str(me.info.user)
    secret = os.environ["NBI_RS_ADMIN_SECRET_ARN"]
    with settings.connect_admin(secret) as admin:
        ensure_tables(admin, names)
        ensure_recon_tables(admin, names.recon)
        grant_pipeline(
            admin,
            (names.raw, names.ledger, names.recon),
            [user],
            lock_tables=[(names.recon, LOCK_TABLE)],
        )
    yield names
    with settings.connect_admin(secret) as admin:
        for schema in (names.raw, names.ledger, names.recon, f"dbt_t_{suffix}"):
            admin.execute(
                sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema))
            )


def _insert(
    conn: RedshiftConnection, schema: str, table: str, rows: list[dict[str, object]]
) -> None:
    for row in rows:
        conn.execute(
            sql.SQL("insert into {} ({}) values ({})").format(
                sql.Identifier(schema, table),
                sql.SQL(", ").join(map(sql.Identifier, row)),
                sql.SQL(", ").join(sql.Placeholder() * len(row)),
            ),
            list(row.values()),
        )


def _dbt_failures(schemas: WarehouseSchemas, test: str, model: str) -> int:
    """Build ``model`` over the throwaway schemas, run ``test``, return its failing rows."""
    env = {
        **os.environ,
        "NBI_DBT_TARGET": "redshift",
        "NBI_DBT_SCHEMA": f"dbt_t_{schemas.raw.removeprefix('t_raw_')}",
    }
    variables = (
        f'{{"raw_schema": "{schemas.raw}", "ledger_schema": "{schemas.ledger}", '
        f'"recon_schema": "{schemas.recon}"}}'
    )

    def dbt(*args: str) -> str:
        result = subprocess.run(  # noqa: S603 - fixed arguments, no shell
            ["dbt", *args, "--vars", variables],  # noqa: S607 - the project's dbt
            cwd=DBT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout

    # Match the node's own result line: the summary's PASS count also counts the
    # on-run-start hooks that number builds (macros/incremental.sql).
    built = dbt("run", "--select", model)
    assert re.search(rf"OK created sql \w+ model \w+\.{model}\b", built), built[-2000:]
    tested = dbt("test", "--select", test)  # this test only, not the model's own
    found = re.search(r"Got (\d+) results?", tested)
    if found is None:
        assert re.search(rf"\d+ of \d+ PASS {test}\b", tested), tested[-2000:]
        return 0
    return int(found.group(1))


def _transition(n: int, **changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "transition_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"t{n}")),
        "exception_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"x{n}")),
        "sequence": 1,
        "from_status": None,
        "to_status": "OPEN",
        "actor": "rule:detected@1",
        "reason": "found",
        "resolution_type": None,
        "changed_at": NOW,
        "run_id": "r",
    }
    row.update(changes)
    return row


def test_the_lifecycle_test_reports_every_transition_postgres_would_refuse(
    schemas: WarehouseSchemas,
) -> None:
    resolved = {"to_status": "RESOLVED", "from_status": "INVESTIGATING"}
    duplicate = str(uuid.uuid5(uuid.NAMESPACE_URL, "dup"))
    rows = [
        _transition(0),  # valid: must not be reported
        _transition(1, sequence=0),
        _transition(2, to_status="BOGUS"),
        _transition(3, actor="bob"),
        _transition(4, reason="  "),
        _transition(5, **resolved, actor="user:a", resolution_type="MAGIC"),
        _transition(6, **resolved, actor="user:a"),  # RESOLVED without a type
        _transition(7, **resolved, actor="rule:x@1", resolution_type="MANUAL_OVERRIDE"),
        _transition(8, **resolved, actor="user:a", resolution_type="AUTO_RULE"),
        _transition(9, exception_id=duplicate),
        _transition(10, exception_id=duplicate),  # same exception and sequence
    ]
    with RedshiftSettings.from_env().connect() as conn:
        conn.autocommit = True
        _insert(conn, schemas.recon, "exception_transitions", rows)
    failures = _dbt_failures(
        schemas,
        "assert_exception_transitions_follow_the_lifecycle",
        "stg_recon__exception_transitions",
    )
    assert failures == 10  # 8 single-rule breaks, plus both rows of the repeated sequence


def test_the_ledger_test_reports_every_line_postgres_would_refuse(
    schemas: WarehouseSchemas,
) -> None:
    def line(n: int, **changes: object) -> dict[str, object]:
        row: dict[str, object] = {
            "journal_line_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"l{n}")),
            "journal_batch_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"b{n}")),
            "line_number": 1,
            "source_event_id": "e",
            "source_event_type": "cash_deposited",
            "posting_rule_id": "p",
            "posting_rule_version": 1,
            "chart_version": "c",
            "ledger_calendar_version": "v",
            "business_date": date(2026, 3, 2),
            "posting_date": date(2026, 3, 2),
            "gl_account": "1000",
            "client_account_id": None,
            "entry_side": "DEBIT",
            "amount": "1.00",
            "currency": "CAD",
            "_ingestion_run_id": "r",
        }
        row.update(changes)
        return row

    batch = str(uuid.uuid5(uuid.NAMESPACE_URL, "dup-batch"))
    rows = [
        line(0),  # valid
        line(1, line_number=0),
        line(2, entry_side="SIDE"),
        line(3, amount="0.00"),
        line(4, journal_batch_id=batch),
        line(5, journal_batch_id=batch),  # same batch and line number
    ]
    with RedshiftSettings.from_env().connect() as conn:
        conn.autocommit = True
        _insert(conn, schemas.ledger, "journal_lines", rows)
    failures = _dbt_failures(
        schemas, "assert_journal_lines_meet_ledger_constraints", "stg_ledger__journal_lines"
    )
    assert failures == 5  # 3 single-rule breaks, plus both rows of the repeated line number
