"""Loading and append-only controls on the live Redshift Serverless workgroup.

Run by hand with ``make test-redshift`` (it costs a few cents of compute), not
in CI. It needs ``aws login`` and the applied Terraform data stack. The tables
are created by the admin, as ``redshift-bootstrap`` does, and every check runs
as the caller's IAM database user, which holds only the pipeline role.
"""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import psycopg
import pytest
from psycopg import sql

from northstar.common.ids import derived_id
from northstar.infrastructure.dialect import RedshiftConnection
from northstar.infrastructure.loading import LoadConflictError
from northstar.infrastructure.postgres import WarehouseSchemas, ensure_tables, insert_new
from northstar.infrastructure.recon_store import (
    LOCK_TABLE,
    TransitionRecord,
    append_transitions,
    ensure_recon_tables,
    lock_exception,
)
from northstar.infrastructure.redshift import (
    RedshiftSettings,
    grant_dashboard_reader,
    grant_pipeline,
)
from northstar.infrastructure.snapshots import (
    PUBLISHED_MARTS,
    VersionRecord,
    ensure_published,
    history,
    log_status,
    record_version,
)

pytestmark = pytest.mark.redshift

COLUMNS = (
    "calendar_day_id",
    "market",
    "calendar_date",
    "is_trading_day",
    "is_settlement_day",
    "trading_holiday_name",
    "settlement_holiday_name",
    "previous_settlement_day",
    "next_settlement_day",
    "calendar_version",
    "_ingestion_run_id",
)


def _day(n: int, version: str = "v1") -> tuple[object, ...]:
    d = date(2026, 3, n)
    return (f"XTSE:{d}", "XTSE", d, True, True, None, None, None, None, version, "run-test")


@pytest.fixture(scope="module")
def settings() -> RedshiftSettings:
    return RedshiftSettings.from_env()


@pytest.fixture(scope="module")
def schemas(settings: RedshiftSettings) -> Iterator[WarehouseSchemas]:
    """Throwaway schemas created and owned by the admin, granted to the caller."""
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
        for schema in (names.raw, names.ledger, names.recon):
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))


@pytest.fixture
def conn(settings: RedshiftSettings) -> Iterator[RedshiftConnection]:
    with settings.connect() as connection:
        connection.autocommit = True
        yield connection


def test_the_pipeline_user_sees_the_tables_the_admin_created(
    conn: RedshiftConnection, schemas: WarehouseSchemas
) -> None:
    assert not conn.admin
    ensure_tables(conn, schemas)  # checks only: the pipeline user cannot create
    ensure_recon_tables(conn, schemas.recon)


def test_loads_are_idempotent_and_refuse_changed_content(
    conn: RedshiftConnection, schemas: WarehouseSchemas
) -> None:
    content = COLUMNS[1:-1]
    rows = [_day(2), _day(3)]
    table = "market_calendar_days"
    assert insert_new(conn, schemas.raw, table, "calendar_day_id", COLUMNS, content, rows) == 2
    assert insert_new(conn, schemas.raw, table, "calendar_day_id", COLUMNS, content, rows) == 0
    more = [_day(3), _day(4)]
    assert insert_new(conn, schemas.raw, table, "calendar_day_id", COLUMNS, content, more) == 1
    with pytest.raises(LoadConflictError, match="stored rows differ"):
        insert_new(conn, schemas.raw, table, "calendar_day_id", COLUMNS, content, [_day(2, "v2")])
    stored = conn.execute(
        sql.SQL("select count(*), max(calendar_version) from {}").format(
            sql.Identifier(schemas.raw, table)
        )
    ).fetchone()
    assert stored == (3, "v1")


def test_journal_amounts_round_trip_exactly(
    conn: RedshiftConnection, schemas: WarehouseSchemas
) -> None:
    columns = (
        "journal_line_id", "journal_batch_id", "line_number", "source_event_id",
        "source_event_type", "posting_rule_id", "posting_rule_version", "chart_version",
        "ledger_calendar_version", "business_date", "posting_date", "gl_account",
        "client_account_id", "entry_side", "amount", "currency", "_ingestion_run_id",
    )  # fmt: skip
    line = uuid.uuid5(uuid.NAMESPACE_URL, "line")
    batch = uuid.uuid5(uuid.NAMESPACE_URL, "batch")
    row = (
        line, batch, 1, "evt_1", "cash_deposited", "cash_deposit", 1, "2026.1", "2026.1",
        date(2026, 3, 2), date(2026, 3, 2), "1000", "acc_1", "DEBIT",
        Decimal("1234567890123456.78"), "CAD", "run-test",
    )  # fmt: skip
    inserted = insert_new(
        conn, schemas.ledger, "journal_lines", "journal_line_id", columns, columns[1:-1], [row]
    )
    assert inserted == 1
    amount, ingested = conn.execute(
        sql.SQL("select amount, _ingested_at from {} where journal_line_id = %s").format(
            sql.Identifier(schemas.ledger, "journal_lines")
        ),
        (str(line),),
    ).fetchone() or (None, None)
    assert amount == Decimal("1234567890123456.78")
    assert isinstance(ingested, datetime)
    assert ingested.astimezone(UTC).year >= 2026


@pytest.mark.parametrize(
    "statement",
    ["update {} set calendar_version = 'x'", "delete from {}", "truncate {}", "lock {}"],
)
def test_the_pipeline_user_cannot_change_or_remove_rows(
    conn: RedshiftConnection, schemas: WarehouseSchemas, statement: str
) -> None:
    table = sql.Identifier(schemas.raw, "market_calendar_days")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(sql.SQL(statement).format(table))


def test_the_lifecycle_lock_works_for_the_pipeline_user(
    conn: RedshiftConnection, schemas: WarehouseSchemas
) -> None:
    conn.autocommit = False
    with conn.transaction():
        assert lock_exception(conn, schemas.recon, str(uuid.uuid4())) is False


@pytest.fixture(scope="module")
def published(settings: RedshiftSettings) -> Iterator[tuple[str, str]]:
    """A stand-in dbt schema with the four gate marts, and a published schema made from it."""
    suffix = uuid.uuid4().hex[:8]
    source, target = f"t_dbt_{suffix}", f"t_pub_{suffix}"
    with settings.connect() as me:
        user = str(me.info.user)
    secret = os.environ["NBI_RS_ADMIN_SECRET_ARN"]
    with settings.connect_admin(secret) as admin:
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(source)))
        for mart in PUBLISHED_MARTS:
            admin.execute(
                sql.SQL(
                    "create table {} (business_date date, amount numeric(18, 2), "
                    "_dbt_invocation_id varchar(4096))"
                ).format(sql.Identifier(source, mart))
            )
            admin.execute(
                sql.SQL("insert into {} values ('2026-03-02', 100.00, 'dbt-run-1')").format(
                    sql.Identifier(source, mart)
                )
            )
        ensure_published(admin, target, source, PUBLISHED_MARTS)
        grant_pipeline(admin, (source, target), [user])
    yield source, target
    with settings.connect_admin(secret) as admin:
        for schema in (source, target):
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))


def test_the_pipeline_user_records_versions_but_cannot_change_them(
    conn: RedshiftConnection, published: tuple[str, str]
) -> None:
    source, target = published
    ensure_published(conn, target, source, PUBLISHED_MARTS)  # checks only
    day, at = date(2026, 3, 2), datetime(2026, 3, 3, 6, 0, tzinfo=UTC)
    hashes = {mart: f"{n:064x}" for n, mart in enumerate(PUBLISHED_MARTS)}
    record = VersionRecord(day, 1, "a" * 64, hashes, "DRAFT", None, None, None)
    with conn.transaction():
        record_version(conn, target, record, 0, "run-test", "sha", source, at)
        log_status(conn, target, day, 1, "PUBLISHED", at, "test")
    (stored,) = history(conn, target, day)
    assert stored.mart_sha256 == hashes  # SUPER round-trips to the same mapping
    assert stored.status == "PUBLISHED"
    for table in ("snapshot_versions", "snapshot_status_log", PUBLISHED_MARTS[0]):
        for statement in ("update {} set business_date = '2026-01-01'", "delete from {}"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql.SQL(statement).format(sql.Identifier(target, table)))


def test_repeated_queries_survive_alter_and_rollback(settings: RedshiftSettings) -> None:
    # psycopg prepares a query after 5 runs and then sends DEALLOCATE ALL after
    # ALTER or ROLLBACK; Redshift rejects that, so prepares must stay off.
    schema = f"t_prep_{uuid.uuid4().hex[:8]}"
    with settings.connect_admin(os.environ["NBI_RS_ADMIN_SECRET_ARN"]) as admin:
        admin.autocommit = True
        admin.execute(sql.SQL("create schema {}").format(sql.Identifier(schema)))
        try:
            table = sql.Identifier(schema, "t")
            admin.execute(sql.SQL("create table {} (a integer)").format(table))
            for _ in range(6):
                admin.execute(
                    "select 1 from information_schema.columns where table_name = %s", ("t",)
                )
            admin.execute(sql.SQL("alter table {} add column b integer").format(table))
            admin.autocommit = False
            admin.execute(sql.SQL("insert into {} values (1, 2)").format(table))
            admin.rollback()
            admin.autocommit = True
        finally:
            admin.execute(sql.SQL("drop schema {} cascade").format(sql.Identifier(schema)))


def test_lifecycle_transitions_are_recorded_with_their_derived_ids(
    conn: RedshiftConnection, schemas: WarehouseSchemas
) -> None:
    # Transition IDs are uuid.UUID values, which psycopg would send as a type
    # Redshift doesn't have; Redshift connections send them as text.
    exception_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "exception-for-transition"))
    record = TransitionRecord(
        exception_id, 1, None, "OPEN", "rule:detected", "detected", None,
        datetime(2026, 3, 3, 6, 0, tzinfo=UTC), "run-test",
    )  # fmt: skip
    conn.autocommit = False
    with conn.transaction():
        assert append_transitions(conn, schemas.recon, [record]) == 1
    conn.autocommit = True
    stored = conn.execute(
        sql.SQL("select transition_id, to_status from {} where exception_id = %s").format(
            sql.Identifier(schemas.recon, "exception_transitions")
        ),
        (exception_id,),
    ).fetchone()
    assert stored == (str(derived_id("recon_transition", exception_id, 1)), "OPEN")


def test_the_dashboard_reader_reads_serving_views_only(
    settings: RedshiftSettings, schemas: WarehouseSchemas
) -> None:
    # A throwaway reader, not the real `looker`: this test must not reset its password.
    suffix = uuid.uuid4().hex[:8]
    serving, reader, password = f"t_serving_{suffix}", f"t_looker_{suffix}", f"Tl{suffix}9x"
    secret = os.environ["NBI_RS_ADMIN_SECRET_ARN"]
    with settings.connect() as me:
        pipeline_user = str(me.info.user)
    with settings.connect_admin(secret) as admin:
        admin.autocommit = True
        grant_dashboard_reader(admin, serving, [pipeline_user], password, reader=reader)
    try:
        # The pipeline creates a view after the grant, as dbt does on every build.
        with settings.connect() as pipeline:
            pipeline.autocommit = True
            pipeline.execute(
                sql.SQL("create view {} as select 1 as figure").format(
                    sql.Identifier(serving, "serving_test")
                )
            )
        with psycopg.connect(
            host=settings.host,
            port=settings.port,
            dbname=settings.dbname,
            user=reader,
            password=password,
            sslmode="require",
            prepare_threshold=None,
        ) as looker:
            looker.autocommit = True
            row = looker.execute(
                sql.SQL("select figure from {}").format(sql.Identifier(serving, "serving_test"))
            ).fetchone()
            assert row == (1,)
            for statement in (
                sql.SQL("select count(*) from {}").format(
                    sql.Identifier(schemas.raw, "market_calendar_days")
                ),
                sql.SQL("create table {} (a integer)").format(sql.Identifier(serving, "t")),
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    looker.execute(statement)
    finally:
        # Default privileges name the reader, so they go before the user can.
        with settings.connect_admin(secret) as admin:
            admin.autocommit = True
            admin.execute(
                sql.SQL(
                    "alter default privileges for user {} in schema {} "
                    "revoke select on tables from {}"
                ).format(
                    sql.Identifier(pipeline_user), sql.Identifier(serving), sql.Identifier(reader)
                )
            )
            admin.execute(
                sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(serving))
            )
            admin.execute(sql.SQL("drop user if exists {}").format(sql.Identifier(reader)))
