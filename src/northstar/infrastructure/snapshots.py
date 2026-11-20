"""Published snapshot store (ADR 006): append-only versions of each business date's marts.

Tables in the published schema, all append-only. On Postgres triggers refuse
UPDATE, DELETE, and TRUNCATE. On Redshift the admin owns the tables
(``redshift-bootstrap``) and the pipeline may only SELECT and INSERT. Redshift
has no CHECK constraints, so ``record_version`` and ``log_status`` enforce the
same rules before every write, on both warehouses:

- ``snapshot_versions``: one immutable row per (business date, version), with
  its content hash, gate result, restatement reason, and what produced it.
- ``snapshot_status_log``: each version's status changes (``DRAFT``,
  ``PUBLISHED``, ``SUPERSEDED``) with their times. Status is an event, never
  an updated column.
- one copy of each published mart (same columns as dbt's), with
  ``snapshot_version``.
- the view ``snapshot_timeline``: each version's current status and its
  published and superseded times, for point-in-time queries.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from northstar.infrastructure.dialect import Dialect, dialect_of, redshift_ddl
from northstar.infrastructure.ordering import canonical_order
from northstar.infrastructure.postgres import make_append_only
from northstar.infrastructure.redshift import require_tables

PUBLISHED_MARTS: Final = (
    "mart_daily_client_cash",
    "mart_daily_executions",
    "mart_daily_finance",
    "mart_daily_positions",
)
"""The financial snapshot: the trusted-data gate marts, published per business date."""

CONTROLS_MARTS: Final = (
    "mart_cash_breaks",
    "mart_exception_monitoring",
    "mart_match_rates",
    "mart_settlement_fails",
)
"""The controls snapshot: reconciliation KPIs (KPI-02 to KPI-06)."""

STATUSES: Final = frozenset({"DRAFT", "PUBLISHED", "SUPERSEDED"})
SNAPSHOT_TABLES: Final = ("snapshot_versions", "snapshot_status_log", "snapshot_timeline")

LINEAGE_ONLY_COLUMNS: Final = frozenset({"_dbt_invocation_id"})
"""Columns kept in published copies but left out of content hashes: they
identify the dbt run, not the data, and differ on every run."""

_VERSIONS_DDL: Final = """
create table if not exists {versions} (
    business_date             date        not null,
    snapshot_version          integer     not null check (snapshot_version >= 1),
    content_sha256            text        not null,
    mart_sha256               jsonb       not null,
    kpi_01_unbalanced_batches bigint      not null,
    restatement_reason        text,
    run_id                    text        not null,
    code_version              text        not null,
    source_schema             text        not null,
    created_at                timestamptz not null,
    primary key (business_date, snapshot_version),
    check ((snapshot_version = 1) = (restatement_reason is null))
)
"""

_STATUS_DDL: Final = """
create table if not exists {log} (
    business_date    date        not null,
    snapshot_version integer     not null,
    status           text        not null
        check (status in ('DRAFT', 'PUBLISHED', 'SUPERSEDED')),
    changed_at       timestamptz not null,
    reason           text        not null,
    primary key (business_date, snapshot_version, status),
    foreign key (business_date, snapshot_version) references {versions}
)
"""

_TIMELINE_DDL: Final = """
create or replace view {timeline} as
select
    v.business_date,
    v.snapshot_version,
    v.content_sha256,
    v.kpi_01_unbalanced_batches,
    v.restatement_reason,
    v.created_at,
    p.changed_at as published_at,
    s.changed_at as superseded_at,
    case
        when s.changed_at is not null then 'SUPERSEDED'
        when p.changed_at is not null then 'PUBLISHED'
        else 'DRAFT'
    end as status
from {versions} as v
left join {log} as p
    on p.business_date = v.business_date
    and p.snapshot_version = v.snapshot_version
    and p.status = 'PUBLISHED'
left join {log} as s
    on s.business_date = v.business_date
    and s.snapshot_version = v.snapshot_version
    and s.status = 'SUPERSEDED'
"""


@dataclass(frozen=True, slots=True)
class VersionRecord:
    """One snapshot version as the timeline reports it."""

    business_date: date
    snapshot_version: int
    content_sha256: str
    mart_sha256: dict[str, str]
    status: str
    published_at: datetime | None
    superseded_at: datetime | None
    restatement_reason: str | None


def ensure_published(
    conn: psycopg.Connection[Any],
    published: str,
    source: str,
    marts: tuple[str, ...] = PUBLISHED_MARTS,
) -> None:
    """Create the published schema, its tables, the timeline view, and append-only triggers.

    Each mart copy is created like the dbt mart in ``source``. A mart whose
    columns later change needs a migration: inserting into an older copy fails.
    On Redshift only the admin creates them (``redshift-bootstrap``, after the
    first dbt build); for any other user this checks that they exist.

    Raises:
        ConfigurationError: On Redshift, a table is missing.
    """
    redshift = dialect_of(conn) is Dialect.REDSHIFT
    if redshift and not getattr(conn, "admin", False):
        require_tables(conn, published, (*SNAPSHOT_TABLES, *marts))
        return
    ddl = redshift_ddl if redshift else str
    names = _names(published)
    with conn.cursor() as cur:
        cur.execute(sql.SQL("create schema if not exists {}").format(sql.Identifier(published)))
        cur.execute(sql.SQL(ddl(_VERSIONS_DDL)).format(**names))
        cur.execute(sql.SQL(ddl(_STATUS_DDL)).format(**names))
        cur.execute(sql.SQL(_TIMELINE_DDL).format(**names))
        for mart in marts:
            copy = sql.Identifier(published, mart)
            cur.execute(
                sql.SQL("create table if not exists {copy} (like {mart})").format(
                    copy=copy, mart=sql.Identifier(source, mart)
                )
            )
            # Redshift has no "add column if not exists".
            cur.execute(
                "select 1 from information_schema.columns where table_schema = %s "
                "and table_name = %s and column_name = 'snapshot_version'",
                (published, mart),
            )
            if cur.fetchone() is None:
                cur.execute(
                    sql.SQL("alter table {copy} add column snapshot_version integer").format(
                        copy=copy
                    )
                )
    for table in ("snapshot_versions", "snapshot_status_log", *marts):
        make_append_only(conn, published, table)


def content_columns(conn: psycopg.Connection[Any], schema: str, mart: str) -> list[str]:
    """The mart's columns that make up its content (all but lineage-only ones), in order."""
    with conn.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns "
            "where table_schema = %s and table_name = %s order by ordinal_position",
            (schema, mart),
        )
        columns = [str(r[0]) for r in cur.fetchall()]
    if not columns:
        msg = f"{schema}.{mart} does not exist"
        raise LookupError(msg)
    return [c for c in columns if c not in LINEAGE_ONLY_COLUMNS and c != "snapshot_version"]


def read_rows(
    conn: psycopg.Connection[Any],
    schema: str,
    mart: str,
    business_date: date,
    columns: Sequence[str],
    snapshot_version: int | None = None,
) -> list[tuple[Any, ...]]:
    """A mart's content rows for one date, in canonical order (``ordering.canonical_order``)."""
    cols = sql.SQL(", ").join(map(sql.Identifier, columns))
    where: sql.Composable = sql.SQL("business_date = %s")
    params: list[Any] = [business_date]
    if snapshot_version is not None:
        where = sql.SQL("{} and snapshot_version = %s").format(where)
        params.append(snapshot_version)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("select {cols} from {table} where {where}").format(
                cols=cols, table=sql.Identifier(schema, mart), where=where
            ),
            params,
        )
        return canonical_order(cur.fetchall())


def unbalanced_batches(conn: psycopg.Connection[Any], source: str, business_date: date) -> int:
    """KPI-01 for one business date, from the dbt controls mart (0 when nothing posted)."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "select coalesce(sum(unbalanced_batch_count), 0) from {} where business_date = %s"
            ).format(sql.Identifier(source, "mart_ledger_balance_controls")),
            (business_date,),
        )
        row = cur.fetchone()
    return int(row[0]) if row is not None else 0


def reporting_dates(conn: psycopg.Connection[Any], source: str) -> list[date]:
    """The business dates dbt reports on (``int_reporting_dates``), ascending."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("select business_date from {} order by business_date").format(
                sql.Identifier(source, "int_reporting_dates")
            )
        )
        return [r[0] for r in cur.fetchall()]


def history(
    conn: psycopg.Connection[Any], published: str, business_date: date
) -> list[VersionRecord]:
    """Every version of one business date, oldest first."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "select t.business_date, t.snapshot_version, t.content_sha256, v.mart_sha256, "
                "t.status, t.published_at, t.superseded_at, t.restatement_reason "
                "from {timeline} as t join {versions} as v "
                "using (business_date, snapshot_version) "
                "where t.business_date = %s order by t.snapshot_version"
            ).format(**_names(published)),
            (business_date,),
        )
        rows = cur.fetchall()
    # Redshift returns SUPER as JSON text; Postgres parses jsonb.
    return [
        VersionRecord(
            business_date=row[0],
            snapshot_version=row[1],
            content_sha256=row[2],
            mart_sha256=json.loads(row[3]) if isinstance(row[3], str) else row[3],
            status=row[4],
            published_at=row[5],
            superseded_at=row[6],
            restatement_reason=row[7],
        )
        for row in rows
    ]


def record_version(
    conn: psycopg.Connection[Any],
    published: str,
    record: VersionRecord,
    kpi_01: int,
    run_id: str,
    code_version: str,
    source: str,
    created_at: datetime,
    marts: tuple[str, ...] = PUBLISHED_MARTS,
) -> None:
    """Insert the version row and copy each mart's rows for the date from ``source``.

    Raises:
        ValueError: The version breaks a rule the Postgres table checks: a
            version number below 1, or a restatement reason missing from a
            restatement or present on a first version.
    """
    if record.snapshot_version < 1:
        msg = f"snapshot version {record.snapshot_version} is below 1"
        raise ValueError(msg)
    if record.snapshot_version == 1 and record.restatement_reason is not None:
        msg = "version 1 is not a restatement and can't have a restatement reason"
        raise ValueError(msg)
    if record.snapshot_version > 1 and record.restatement_reason is None:
        msg = f"version {record.snapshot_version} is a restatement and needs a reason"
        raise ValueError(msg)
    redshift = dialect_of(conn) is Dialect.REDSHIFT
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "insert into {versions} (business_date, snapshot_version, content_sha256, "
                "mart_sha256, kpi_01_unbalanced_batches, restatement_reason, run_id, "
                "code_version, source_schema, created_at) "
                "values (%s, %s, %s, {json}, %s, %s, %s, %s, %s, %s)"
            ).format(json=sql.SQL("json_parse(%s)" if redshift else "%s"), **_names(published)),
            (
                record.business_date,
                record.snapshot_version,
                record.content_sha256,
                json.dumps(record.mart_sha256, sort_keys=True)
                if redshift
                else Jsonb(record.mart_sha256),
                kpi_01,
                record.restatement_reason,
                run_id,
                code_version,
                source,
                created_at,
            ),
        )
        for mart in marts:
            columns = [
                *content_columns(conn, source, mart),
                *sorted(LINEAGE_ONLY_COLUMNS),
            ]
            cols = sql.SQL(", ").join(map(sql.Identifier, columns))
            cur.execute(
                sql.SQL(
                    "insert into {copy} ({cols}, snapshot_version) "
                    "select {cols}, %s from {mart} where business_date = %s"
                ).format(
                    copy=sql.Identifier(published, mart),
                    mart=sql.Identifier(source, mart),
                    cols=cols,
                ),
                (record.snapshot_version, record.business_date),
            )


def log_status(
    conn: psycopg.Connection[Any],
    published: str,
    business_date: date,
    snapshot_version: int,
    status: str,
    changed_at: datetime,
    reason: str,
) -> None:
    """Append one status change for a version.

    Raises:
        ValueError: ``status`` is not DRAFT, PUBLISHED, or SUPERSEDED.
    """
    if status not in STATUSES:
        msg = f"unknown snapshot status {status!r}"
        raise ValueError(msg)
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "insert into {log} (business_date, snapshot_version, status, changed_at, reason) "
                "values (%s, %s, %s, %s, %s)"
            ).format(**_names(published)),
            (business_date, snapshot_version, status, changed_at, reason),
        )


def read_as_published(
    conn: psycopg.Connection[Any],
    published: str,
    mart: str,
    business_date: date,
    as_of: datetime,
) -> tuple[int | None, list[tuple[Any, ...]]]:
    """The version of ``mart`` for ``business_date`` that was published at ``as_of``, and its rows.

    Returns ``(None, [])`` if nothing was published for that date by then.
    """
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "select snapshot_version from {timeline} where business_date = %s "
                "and published_at <= %s and (superseded_at is null or superseded_at > %s)"
            ).format(**_names(published)),
            (business_date, as_of, as_of),
        )
        row = cur.fetchone()
    if row is None:
        return None, []
    version = int(row[0])
    columns = content_columns(conn, published, mart)
    return version, read_rows(conn, published, mart, business_date, columns, version)


def _names(published: str) -> dict[str, sql.Identifier]:
    return {
        "versions": sql.Identifier(published, "snapshot_versions"),
        "log": sql.Identifier(published, "snapshot_status_log"),
        "timeline": sql.Identifier(published, "snapshot_timeline"),
    }
