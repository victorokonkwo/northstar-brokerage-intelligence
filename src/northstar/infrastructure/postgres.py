"""Load canonical events and journal lines into the warehouse Postgres.

Loads are **idempotent**: rows are keyed by ``event_id`` and
``journal_line_id``, so replaying the same input inserts nothing new
(FR-M1-03, INV-02 for this slice). A replay that disagrees with what is
already stored is a corrupted source and raises ``LoadConflictError``; it is
never ignored. Posted journal lines are only ever inserted (ADR 002).
"""

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

import psycopg
from psycopg import sql

from northstar.common.exceptions import ConfigurationError, NorthstarError
from northstar.domain.accounting import JournalBatch
from northstar.domain.events import Event
from northstar.infrastructure.event_files import serialize_event


class LoadConflictError(NorthstarError):
    """Raised when a replayed row has the same key but different content."""


@dataclass(frozen=True, slots=True)
class PostgresSettings:
    """Connection settings for the warehouse database. Never logged."""

    host: str
    port: int
    dbname: str
    user: str
    password: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "PostgresSettings":
        """Read the ``NBI_PG_*`` variables (see ``.env.example``).

        Raises:
            ConfigurationError: A variable is missing or the port is not a number.
        """
        env = os.environ if environ is None else environ
        names = ("NBI_PG_HOST", "NBI_PG_PORT", "NBI_PG_DATABASE", "NBI_PG_USER", "NBI_PG_PASSWORD")
        missing = [name for name in names if not env.get(name)]
        if missing:
            msg = f"missing environment variables: {', '.join(missing)} (run `make env`)"
            raise ConfigurationError(msg)
        try:
            port = int(env["NBI_PG_PORT"])
        except ValueError as error:
            msg = f"NBI_PG_PORT must be a number, got {env['NBI_PG_PORT']!r}"
            raise ConfigurationError(msg) from error
        return cls(
            host=env["NBI_PG_HOST"],
            port=port,
            dbname=env["NBI_PG_DATABASE"],
            user=env["NBI_PG_USER"],
            password=env["NBI_PG_PASSWORD"],
        )

    def connect(self) -> psycopg.Connection[Any]:
        """Open a new connection. The caller owns it and must close it."""
        return psycopg.connect(
            host=self.host,
            port=self.port,
            dbname=self.dbname,
            user=self.user,
            password=self.password,
        )


@dataclass(frozen=True, slots=True)
class WarehouseSchemas:
    """Schema names for the raw and ledger tables. Tests use throwaway names."""

    raw: str = "raw"
    ledger: str = "ledger"


_EVENTS_DDL: Final = """
create table if not exists {raw}.brokerage_events (
    event_id          text        primary key,
    event_type        text        not null,
    schema_version    integer     not null,
    source_system     text        not null,
    event_timestamp   timestamptz not null,
    recorded_at       timestamptz not null,
    correlation_id    text        not null,
    causation_id      text,
    payload           jsonb       not null,
    _ingestion_run_id text        not null,
    _ingested_at      timestamptz not null default now()
)
"""

_JOURNAL_DDL: Final = """
create table if not exists {ledger}.journal_lines (
    journal_line_id         uuid          primary key,
    journal_batch_id        uuid          not null,
    line_number             integer       not null check (line_number >= 1),
    source_event_id         text          not null,
    source_event_type       text          not null,
    posting_rule_id         text          not null,
    posting_rule_version    integer       not null,
    chart_version           text          not null,
    ledger_calendar_version text          not null,
    business_date           date          not null,
    posting_date            date          not null,
    gl_account              char(4)       not null,
    client_account_id       text,
    entry_side              text          not null check (entry_side in ('DEBIT', 'CREDIT')),
    amount                  numeric(18,2) not null check (amount > 0),
    currency                char(3)       not null,
    _ingestion_run_id       text          not null,
    _ingested_at            timestamptz   not null default now(),
    unique (journal_batch_id, line_number)
)
"""

_EXTERNAL_DDL: Final = """
create table if not exists {raw}.external_records (
    record_id         text        primary key,
    record_set        text        not null,
    event_date        date        not null,
    schema_version    integer     not null,
    payload           jsonb       not null,
    _ingestion_run_id text        not null,
    _ingested_at      timestamptz not null default now()
)
"""

_CALENDAR_DDL: Final = """
create table if not exists {raw}.market_calendar_days (
    calendar_day_id          text    primary key,
    market                   text    not null,
    calendar_date            date    not null,
    is_trading_day           boolean not null,
    is_settlement_day        boolean not null,
    trading_holiday_name     text,
    settlement_holiday_name  text,
    previous_settlement_day  date,
    next_settlement_day      date,
    calendar_version         text    not null,
    _ingestion_run_id        text    not null,
    _ingested_at             timestamptz not null default now(),
    unique (market, calendar_date)
)
"""

_APPEND_ONLY_DDL: Final = """
create or replace function {schema}.refuse_change() returns trigger
language plpgsql as $$
begin
    raise exception '% on %.% is refused: the table is append-only (INV-11)',
        tg_op, tg_table_schema, tg_table_name;
end
$$
"""

_APPEND_ONLY_TRIGGERS: Final = (
    "create or replace trigger refuse_update_delete before update or delete on {table} "
    "for each row execute function {function}()",
    "create or replace trigger refuse_truncate before truncate on {table} "
    "for each statement execute function {function}()",
)

_CALENDAR_COLUMNS: Final = (
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
_CALENDAR_CONTENT: Final = _CALENDAR_COLUMNS[1:-1]

_EXTERNAL_COLUMNS: Final = (
    "record_id",
    "record_set",
    "event_date",
    "schema_version",
    "payload",
    "_ingestion_run_id",
)
_EXTERNAL_CONTENT: Final = _EXTERNAL_COLUMNS[1:-1]

_EVENT_COLUMNS: Final = (
    "event_id",
    "event_type",
    "schema_version",
    "source_system",
    "event_timestamp",
    "recorded_at",
    "correlation_id",
    "causation_id",
    "payload",
    "_ingestion_run_id",
)
_EVENT_CONTENT: Final = _EVENT_COLUMNS[1:-1]  # compared on replay; run ID may differ

_JOURNAL_COLUMNS: Final = (
    "journal_line_id",
    "journal_batch_id",
    "line_number",
    "source_event_id",
    "source_event_type",
    "posting_rule_id",
    "posting_rule_version",
    "chart_version",
    "ledger_calendar_version",
    "business_date",
    "posting_date",
    "gl_account",
    "client_account_id",
    "entry_side",
    "amount",
    "currency",
    "_ingestion_run_id",
)
_JOURNAL_CONTENT: Final = _JOURNAL_COLUMNS[1:-1]


def ensure_tables(conn: psycopg.Connection[Any], schemas: WarehouseSchemas) -> None:
    """Create the raw and ledger schemas and tables if they do not exist."""
    with conn.cursor() as cur:
        for name in (schemas.raw, schemas.ledger):
            cur.execute(sql.SQL("create schema if not exists {}").format(sql.Identifier(name)))
        cur.execute(sql.SQL(_EVENTS_DDL).format(raw=sql.Identifier(schemas.raw)))
        cur.execute(sql.SQL(_JOURNAL_DDL).format(ledger=sql.Identifier(schemas.ledger)))
        cur.execute(sql.SQL(_EXTERNAL_DDL).format(raw=sql.Identifier(schemas.raw)))
        cur.execute(sql.SQL(_CALENDAR_DDL).format(raw=sql.Identifier(schemas.raw)))
        # Loaded rows are never updated or deleted (W2.4, W3.3, INV-11).
        for schema, table in (
            (schemas.raw, "brokerage_events"),
            (schemas.raw, "external_records"),
            (schemas.raw, "market_calendar_days"),
            (schemas.ledger, "journal_lines"),
        ):
            function = sql.Identifier(schema, "refuse_change")
            cur.execute(sql.SQL(_APPEND_ONLY_DDL).format(schema=sql.Identifier(schema)))
            for trigger in _APPEND_ONLY_TRIGGERS:
                cur.execute(
                    sql.SQL(trigger).format(table=sql.Identifier(schema, table), function=function)
                )


def load_events(
    conn: psycopg.Connection[Any], events: Iterable[Event], run_id: str, schemas: WarehouseSchemas
) -> int:
    """Insert events that are not already stored; return how many were new.

    Raises:
        LoadConflictError: A stored event has the same ``event_id`` but
            different content.
    """
    rows = []
    for event in events:
        record = json.loads(serialize_event(event))
        rows.append(
            (
                record["event_id"],
                record["event_type"],
                record["schema_version"],
                record["source_system"],
                datetime.fromisoformat(record["event_timestamp"]),
                datetime.fromisoformat(record["recorded_at"]),
                record["correlation_id"],
                record["causation_id"],
                json.dumps(record["payload"], sort_keys=True, separators=(",", ":")),
                run_id,
            )
        )
    return _insert_new(
        conn, schemas.raw, "brokerage_events", "event_id", _EVENT_COLUMNS, _EVENT_CONTENT, rows
    )


def load_journal(
    conn: psycopg.Connection[Any],
    batches: Iterable[JournalBatch],
    run_id: str,
    schemas: WarehouseSchemas,
) -> int:
    """Insert journal lines that are not already stored; return how many were new.

    Raises:
        LoadConflictError: A stored line has the same ``journal_line_id`` but
            different content, which would mean a posting rule changed
            without a version bump.
    """
    rows = [
        (
            line.journal_line_id,
            batch.journal_batch_id,
            line.line_number,
            batch.source_event_id,
            str(batch.source_event_type),
            batch.posting_rule_id,
            batch.posting_rule_version,
            batch.chart_version,
            batch.ledger_calendar_version,
            batch.business_date,
            batch.posting_date,
            line.gl_account,
            line.client_account_id,
            line.side.value,
            line.amount.amount,
            str(line.amount.currency),
            run_id,
        )
        for batch in batches
        for line in batch.lines
    ]
    return _insert_new(
        conn,
        schemas.ledger,
        "journal_lines",
        "journal_line_id",
        _JOURNAL_COLUMNS,
        _JOURNAL_CONTENT,
        rows,
    )


@dataclass(frozen=True, slots=True)
class ExternalRow:
    """One external record as stored in ``raw.external_records``."""

    record_id: str
    record_set: str
    event_date: date
    schema_version: int
    payload: str  # the record's JSON, as received


def load_external_records(
    conn: psycopg.Connection[Any],
    records: Iterable[ExternalRow],
    run_id: str,
    schemas: WarehouseSchemas,
) -> int:
    """Insert external records that are not already stored; return how many were new.

    Raises:
        LoadConflictError: A stored record has the same ``record_id`` but
            different content.
    """
    rows = [
        (
            r.record_id,
            r.record_set,
            r.event_date,
            r.schema_version,
            json.dumps(json.loads(r.payload), sort_keys=True, separators=(",", ":")),
            run_id,
        )
        for r in records
    ]
    return _insert_new(
        conn,
        schemas.raw,
        "external_records",
        "record_id",
        _EXTERNAL_COLUMNS,
        _EXTERNAL_CONTENT,
        rows,
    )


@dataclass(frozen=True, slots=True)
class CalendarDay:
    """One market's calendar on one date, as the domain calendar computes it."""

    market: str
    calendar_date: date
    is_trading_day: bool
    is_settlement_day: bool
    trading_holiday_name: str | None
    settlement_holiday_name: str | None
    previous_settlement_day: date | None  # None where the calendar's coverage ends
    next_settlement_day: date | None
    calendar_version: str


def load_calendar_days(
    conn: psycopg.Connection[Any],
    days: Iterable[CalendarDay],
    run_id: str,
    schemas: WarehouseSchemas,
) -> int:
    """Insert calendar days that are not already stored; return how many were new.

    A stored day that differs (a corrected calendar) is refused, not
    overwritten: changing a calendar that results were computed with is a
    restatement and needs its own migration.

    Raises:
        LoadConflictError: A stored day differs from the configured calendar.
    """
    rows = [
        (
            f"{d.market}:{d.calendar_date.isoformat()}",
            d.market,
            d.calendar_date,
            d.is_trading_day,
            d.is_settlement_day,
            d.trading_holiday_name,
            d.settlement_holiday_name,
            d.previous_settlement_day,
            d.next_settlement_day,
            d.calendar_version,
            run_id,
        )
        for d in days
    ]
    return _insert_new(
        conn,
        schemas.raw,
        "market_calendar_days",
        "calendar_day_id",
        _CALENDAR_COLUMNS,
        _CALENDAR_CONTENT,
        rows,
    )


def _insert_new(
    conn: psycopg.Connection[Any],
    schema: str,
    table: str,
    key: str,
    columns: Sequence[str],
    content: Sequence[str],
    rows: Sequence[tuple[Any, ...]],
) -> int:
    by_key: dict[Any, tuple[Any, ...]] = {}
    for row in rows:
        previous = by_key.setdefault(row[0], row)
        if previous[1:-1] != row[1:-1]:
            msg = f"{schema}.{table}: the input repeats {key} {row[0]} with different content"
            raise LoadConflictError(msg)
    target = sql.Identifier(schema, table)
    staging = sql.Identifier(f"_load_{table}")
    cols = sql.SQL(", ").join(map(sql.Identifier, columns))
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "create temp table {staging} (like {target} including defaults) on commit drop"
            ).format(staging=staging, target=target)
        )
        with cur.copy(
            sql.SQL("copy {staging} ({cols}) from stdin").format(staging=staging, cols=cols)
        ) as copy:
            for row in rows:
                copy.write_row(row)
        differs = sql.SQL(" or ").join(
            sql.SQL("s.{c} is distinct from t.{c}").format(c=sql.Identifier(c)) for c in content
        )
        cur.execute(
            sql.SQL(
                "select s.{key} from {staging} s join {target} t using ({key}) "
                "where {differs} limit 5"
            ).format(key=sql.Identifier(key), staging=staging, target=target, differs=differs)
        )
        conflicts = [str(r[0]) for r in cur.fetchall()]
        if conflicts:
            msg = f"{schema}.{table}: stored rows differ from the replay for {key} {conflicts}"
            raise LoadConflictError(msg)
        cur.execute(
            sql.SQL(
                "insert into {target} ({cols}) select {cols} from {staging} "
                "on conflict ({key}) do nothing"
            ).format(target=target, cols=cols, staging=staging, key=sql.Identifier(key))
        )
        inserted = cur.rowcount
        cur.execute(sql.SQL("drop table {staging}").format(staging=staging))
    return inserted
