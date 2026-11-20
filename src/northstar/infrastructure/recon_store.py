"""Reconciliation inputs from the warehouse, and the append-only ``recon`` schema.

Inputs come from the dbt models: the internal side from core facts and the
journal, the external side from ``stg_external__*``. Only records dated on or
before the run's as-of date are read, so a run sees what was known by then.

Outputs, all append-only (triggers refuse UPDATE, DELETE, and TRUNCATE):

- ``runs``: one row per reconciliation run (as-of date, policy and code version)
- ``match_results``: one row per match, keyed by a deterministic ID
- ``exceptions``: one row per classified exception, keyed by a deterministic ID

A rerun with the same inputs finds the same IDs and inserts nothing (FR-M2-07);
a stored row whose content differs is refused (``LoadConflictError``).
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final

import psycopg
from psycopg import sql

from northstar.common.ids import derived_id
from northstar.common.money import Currency
from northstar.domain.matching import (
    ExternalCash,
    ExternalExecution,
    ExternalFee,
    ExternalFx,
    ExternalPosition,
    ExternalPrice,
    ExternalSettlement,
    Inputs,
    InternalCash,
    InternalExecution,
    InternalFee,
    InternalFx,
    InternalPosition,
    InternalPrice,
    InternalSettlement,
    Match,
)
from northstar.domain.reconciliation import AppliedRate, ReconException
from northstar.infrastructure.postgres import insert_new, make_append_only

_RUNS_DDL: Final = """
create table if not exists {t} (
    run_id         text        primary key,
    as_of          date        not null,
    source_schema  text        not null,
    policy_version text        not null,
    code_version   text        not null,
    matches        integer     not null,
    exceptions     integer     not null,
    created_at     timestamptz not null
)
"""

_MATCHES_DDL: Final = """
create table if not exists {t} (
    match_id            uuid    primary key,
    reconciliation      text    not null,
    rule_id             text    not null,
    rule_version        integer not null,
    internal_ref        text    not null,
    external_record_ids text    not null,
    business_date       date    not null,
    run_id              text    not null,
    _ingested_at        timestamptz not null default now()
)
"""

_EXCEPTIONS_DDL: Final = """
create table if not exists {t} (
    exception_id        uuid          primary key,
    reconciliation      text          not null,
    exception_type      text          not null,
    severity            text          not null,
    reference           text          not null,
    business_date       date          not null,
    impact_amount       numeric(18,8) not null,
    impact_currency     char(3)       not null,
    impact_basis        text          not null,
    impact_cad          numeric(18,2),
    fx_rate_date        date,
    fx_policy_version   text,
    external_record_ids text          not null,
    detail              text          not null,
    policy_version      text          not null,
    run_id              text          not null,
    _ingested_at        timestamptz   not null default now()
)
"""

_TRANSITIONS_DDL: Final = """
create table if not exists {t} (
    transition_id   uuid        primary key,
    exception_id    uuid        not null references {exceptions},
    sequence        integer     not null check (sequence >= 1),
    from_status     text,
    to_status       text        not null
        check (to_status in ('OPEN', 'INVESTIGATING', 'ESCALATED', 'RESOLVED')),
    actor           text        not null check (actor like 'rule:%' or actor like 'user:%'),
    reason          text        not null check (length(trim(reason)) > 0),
    resolution_type text
        check (resolution_type in ('AUTO_RULE', 'MANUAL_OVERRIDE', 'SOURCE_CORRECTED')),
    changed_at      timestamptz not null,
    run_id          text,
    unique (exception_id, sequence),
    -- FR-M2-06, enforced by the database as well as the domain:
    check ((to_status = 'RESOLVED') = (resolution_type is not null)),
    check (resolution_type is distinct from 'MANUAL_OVERRIDE' or actor like 'user:%'),
    check (resolution_type not in ('AUTO_RULE', 'SOURCE_CORRECTED') or actor like 'rule:%')
)
"""

_MATCH_COLUMNS: Final = (
    "match_id",
    "reconciliation",
    "rule_id",
    "rule_version",
    "internal_ref",
    "external_record_ids",
    "business_date",
    "run_id",
)
_EXCEPTION_COLUMNS: Final = (
    "exception_id",
    "reconciliation",
    "exception_type",
    "severity",
    "reference",
    "business_date",
    "impact_amount",
    "impact_currency",
    "impact_basis",
    "impact_cad",
    "fx_rate_date",
    "fx_policy_version",
    "external_record_ids",
    "detail",
    "policy_version",
    "run_id",
)


def ensure_recon_tables(conn: psycopg.Connection[Any], schema: str) -> None:
    """Create the recon schema and its append-only tables if they do not exist."""
    with conn.cursor() as cur:
        cur.execute(sql.SQL("create schema if not exists {}").format(sql.Identifier(schema)))
        for ddl, table in (
            (_RUNS_DDL, "runs"),
            (_MATCHES_DDL, "match_results"),
            (_EXCEPTIONS_DDL, "exceptions"),
            (_TRANSITIONS_DDL, "exception_transitions"),
        ):
            cur.execute(
                sql.SQL(ddl).format(
                    t=sql.Identifier(schema, table),
                    exceptions=sql.Identifier(schema, "exceptions"),
                )
            )
    for table in ("runs", "match_results", "exceptions", "exception_transitions"):
        make_append_only(conn, schema, table)


@dataclass(frozen=True, slots=True)
class RunRecord:
    """What produced a reconciliation run (W4.3)."""

    run_id: str
    as_of: date
    source_schema: str
    policy_version: str
    code_version: str
    created_at: datetime


def store_run(
    conn: psycopg.Connection[Any],
    schema: str,
    run: RunRecord,
    matches: tuple[Match, ...],
    exceptions: tuple[ReconException, ...],
) -> tuple[int, int]:
    """Store a run's matches and exceptions; return how many of each were new."""
    match_rows = [
        (
            derived_id(
                "recon_match",
                m.reconciliation,
                m.rule_id,
                m.rule_version,
                m.internal_ref,
                *m.external_record_ids,
            ),
            str(m.reconciliation),
            m.rule_id,
            m.rule_version,
            m.internal_ref,
            ",".join(m.external_record_ids),
            m.business_date,
            run.run_id,
        )
        for m in matches
    ]
    exception_rows = [
        (
            x.exception_id,
            x.reconciliation,
            str(x.exception_type),
            str(x.severity),
            x.reference,
            x.business_date,
            x.impact_amount,
            str(x.impact_currency),
            x.impact_basis,
            x.impact_cad,
            x.fx_rate_date,
            x.fx_policy_version,
            ",".join(x.external_record_ids),
            x.detail,
            x.policy_version,
            run.run_id,
        )
        for x in exceptions
    ]
    new_matches = insert_new(
        conn, schema, "match_results", "match_id", _MATCH_COLUMNS, _MATCH_COLUMNS[1:-1], match_rows
    )
    new_exceptions = insert_new(
        conn,
        schema,
        "exceptions",
        "exception_id",
        _EXCEPTION_COLUMNS,
        _EXCEPTION_COLUMNS[1:-1],
        exception_rows,
    )
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "insert into {} (run_id, as_of, source_schema, policy_version, code_version, "
                "matches, exceptions, created_at) values (%s, %s, %s, %s, %s, %s, %s, %s)"
            ).format(sql.Identifier(schema, "runs")),
            (
                run.run_id,
                run.as_of,
                run.source_schema,
                run.policy_version,
                run.code_version,
                len(matches),
                len(exceptions),
                run.created_at,
            ),
        )
    return new_matches, new_exceptions


def stored_exceptions(conn: psycopg.Connection[Any], schema: str) -> list[dict[str, Any]]:
    """Every stored exception, as column-name dictionaries."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("select {} from {} order by exception_id").format(
                sql.SQL(", ").join(map(sql.Identifier, _EXCEPTION_COLUMNS)),
                sql.Identifier(schema, "exceptions"),
            )
        )
        return [dict(zip(_EXCEPTION_COLUMNS, row, strict=True)) for row in cur.fetchall()]


def last_reporting_date(conn: psycopg.Connection[Any], source: str) -> date:
    """The latest reporting date dbt built (``int_reporting_dates``)."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL("select max(business_date) from {}").format(
                sql.Identifier(source, "int_reporting_dates")
            )
        )
        row = cur.fetchone()
    if row is None or row[0] is None:
        msg = f"{source}.int_reporting_dates is empty"
        raise LookupError(msg)
    value: date = row[0]
    return value


def read_inputs(
    conn: psycopg.Connection[Any], source: str, as_of: date
) -> tuple[Inputs, dict[date, AppliedRate]]:
    """Both sides of every reconciliation, and the applicable rates, as known on ``as_of``."""

    def rows(query: str) -> list[tuple[Any, ...]]:
        with conn.cursor() as cur:
            cur.execute(sql.SQL(query).format(s=sql.Identifier(source)), {"as_of": as_of})
            return list(cur.fetchall())

    def ccy(code: str) -> Currency:
        return Currency(code.strip())

    executions = [
        InternalExecution(r[0], r[1], r[2], r[3], r[4], r[5], ccy(r[6]), r[7], r[8])
        for r in rows(
            "select e.execution_id, e.account_id, e.instrument_id, e.side, e.quantity, e.price, "
            "e.currency, e.trade_date, a.gross_amount from {s}.fct_executions e "
            "join {s}.int_execution_amounts a using (execution_id) "
            "where e.trade_date <= %(as_of)s order by e.execution_id"
        )
    ]
    fees = [
        InternalFee(r[0], r[1], r[2], ccy(r[3]), r[4])
        for r in rows(
            "select a.execution_id, e.account_id, a.clearing_fee, a.currency, a.trade_date "
            "from {s}.int_execution_amounts a join {s}.fct_executions e using (execution_id) "
            "where a.clearing_fee > 0 and a.trade_date <= %(as_of)s order by a.execution_id"
        )
    ]
    multipliers = {
        r[0]: int(r[1])
        for r in rows("select instrument_id, multiplier from {s}.dim_instrument where is_current")
    }
    settlements = [
        InternalSettlement(r[0], r[1], r[2], r[3], r[4], ccy(r[5]), r[6])
        for r in rows(
            "select settlement_id, account_id, instrument_id, settled_quantity, settled_amount, "
            "currency, contractual_settlement_date from {s}.fct_settlements "
            "where settlement_status = 'SETTLED' and contractual_settlement_date <= %(as_of)s "
            "order by settlement_id"
        )
    ]
    # GL 1000 movements, signed from the bank's view, by the reference the bank echoes.
    cash_totals: dict[tuple[str, Currency, date], Decimal] = defaultdict(Decimal)
    for ref, currency, day, amount in rows(
        "select coalesce(d.cash_movement_id, w.cash_movement_id, t.settlement_id), j.currency, "
        "j.posting_date, j.debit_amount - j.credit_amount from {s}.fct_journal_lines j "
        "left join {s}.stg_brokerage__cash_deposited d on j._source_event_id = d.event_id "
        "left join {s}.stg_brokerage__cash_withdrawn w on j._source_event_id = w.event_id "
        "left join {s}.stg_brokerage__trade_settled t on j._source_event_id = t.event_id "
        "where j.gl_account = '1000' and j.posting_date <= %(as_of)s"
    ):
        if ref is None:
            msg = "a GL 1000 line has no bank reference (an unexpected source event)"
            raise LookupError(msg)
        cash_totals[(ref, ccy(currency), day)] += amount
    cash = [InternalCash(r, a, c, d) for (r, c, d), a in sorted(cash_totals.items())]
    positions = [
        InternalPosition(r[0], r[1], r[2], r[3], ccy(r[4]))
        for r in rows(
            "select account_id, instrument_id, business_date, settled_quantity, currency "
            "from {s}.fct_positions_daily where business_date <= %(as_of)s"
        )
    ]
    fx = [
        InternalFx(r[0], r[1], r[2], ccy(r[3]), r[4], ccy(r[5]), r[6])
        for r in rows(
            "select cash_movement_id, account_id, amount_out, currency_out, amount_in, "
            "currency_in, business_date from {s}.fct_cash_movements "
            "where movement_type = 'FX_CONVERSION' and business_date <= %(as_of)s "
            "order by cash_movement_id"
        )
    ]
    prices = [
        InternalPrice(r[0], r[1], r[2], ccy(r[3]))
        for r in rows(
            "select instrument_id, price_date, close_price, currency "
            "from {s}.stg_brokerage__price_updated where price_date <= %(as_of)s"
        )
    ]
    clearing = [
        ExternalExecution(r[0], r[1], r[2], r[3], r[4], r[5], r[6], ccy(r[7]), r[8])
        for r in rows(
            "select record_id, broker_reference, account_id, instrument_id, side, quantity, "
            "price, currency, trade_date from {s}.stg_external__clearing_confirmations "
            "where event_date <= %(as_of)s order by record_id"
        )
    ]
    confirmations = [
        ExternalSettlement(r[0], r[1], r[2], r[3], r[4], r[5], ccy(r[6]), r[7], r[8])
        for r in rows(
            "select record_id, broker_reference, account_id, instrument_id, quantity, amount, "
            "currency, contractual_settlement_date, actual_settlement_date "
            "from {s}.stg_external__settlement_confirmations "
            "where event_date <= %(as_of)s order by record_id"
        )
    ]
    bank = [
        ExternalCash(r[0], r[1], r[2], ccy(r[3]), r[4])
        for r in rows(
            "select record_id, reference, amount, currency, value_date "
            "from {s}.stg_external__bank_statement_lines "
            "where event_date <= %(as_of)s order by record_id"
        )
    ]
    custody = [
        ExternalPosition(r[0], r[1], r[2], r[3], r[4])
        for r in rows(
            "select record_id, account_id, instrument_id, snapshot_date, settled_quantity "
            "from {s}.stg_external__custody_snapshots "
            "where event_date <= %(as_of)s order by record_id"
        )
    ]
    fee_records = [
        ExternalFee(r[0], r[1], r[2], r[3], ccy(r[4]), r[5])
        for r in rows(
            "select record_id, broker_reference, account_id, amount, currency, trade_date "
            "from {s}.stg_external__fee_records "
            "where event_date <= %(as_of)s order by record_id"
        )
    ]
    fx_confirmations = [
        ExternalFx(r[0], r[1], r[2], r[3], ccy(r[4]), r[5], ccy(r[6]), r[7])
        for r in rows(
            "select record_id, broker_reference, account_id, sell_amount, sell_currency, "
            "buy_amount, buy_currency, trade_date from {s}.stg_external__fx_confirmations "
            "where event_date <= %(as_of)s order by record_id"
        )
    ]
    price_feed = [
        ExternalPrice(r[0], r[1], r[2], r[3], ccy(r[4]))
        for r in rows(
            "select record_id, instrument_id, price_date, close_price, currency "
            "from {s}.stg_external__price_feed "
            "where event_date <= %(as_of)s order by record_id"
        )
    ]
    rates = {
        r[0]: AppliedRate(r[1], r[2], r[3])
        for r in rows(
            "select calendar_date, rate, fx_rate_date, fx_policy_version "
            "from {s}.int_fx_rates_applicable where calendar_date <= %(as_of)s"
        )
    }
    inputs = Inputs(
        executions=executions,
        clearing=clearing,
        multipliers=multipliers,
        settlements=settlements,
        settlement_confirmations=confirmations,
        cash=cash,
        bank=bank,
        positions=positions,
        custody=custody,
        fees=fees,
        fee_records=fee_records,
        fx=fx,
        fx_confirmations=fx_confirmations,
        prices=prices,
        price_feed=price_feed,
    )
    return inputs, rates


@dataclass(frozen=True, slots=True)
class ExceptionState:
    """An exception with its current status (from its latest transition)."""

    exception_id: str
    exception_type: str
    severity: str
    reference: str
    business_date: date
    status: str | None  # None before the first transition
    sequence: int


def current_states(conn: psycopg.Connection[Any], schema: str) -> dict[str, ExceptionState]:
    """Every exception and its current status."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "with latest as (select exception_id, to_status, sequence, row_number() over "
                "(partition by exception_id order by sequence desc) as n from {transitions}) "
                "select e.exception_id::text, e.exception_type, e.severity, e.reference, "
                "e.business_date, t.to_status, coalesce(t.sequence, 0) "
                "from {exceptions} e left join latest t "
                "on t.exception_id = e.exception_id and t.n = 1"
            ).format(
                exceptions=sql.Identifier(schema, "exceptions"),
                transitions=sql.Identifier(schema, "exception_transitions"),
            )
        )
        return {r[0]: ExceptionState(*r) for r in cur.fetchall()}


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """A transition to append (already checked by ``northstar.domain.lifecycle.check``)."""

    exception_id: str
    sequence: int
    from_status: str | None
    to_status: str
    actor: str
    reason: str
    resolution_type: str | None
    changed_at: datetime
    run_id: str | None


def append_transitions(
    conn: psycopg.Connection[Any], schema: str, transitions: list[TransitionRecord]
) -> int:
    """Append transitions; return how many. A repeated (exception, sequence) is refused."""
    with conn.cursor() as cur:
        for t in transitions:
            cur.execute(
                sql.SQL(
                    "insert into {} (transition_id, exception_id, sequence, from_status, "
                    "to_status, actor, reason, resolution_type, changed_at, run_id) "
                    "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                ).format(sql.Identifier(schema, "exception_transitions")),
                (
                    derived_id("recon_transition", t.exception_id, t.sequence),
                    t.exception_id,
                    t.sequence,
                    t.from_status,
                    t.to_status,
                    t.actor,
                    t.reason,
                    t.resolution_type,
                    t.changed_at,
                    t.run_id,
                ),
            )
    return len(transitions)


def history(
    conn: psycopg.Connection[Any], schema: str, exception_id: str
) -> list[TransitionRecord]:
    """An exception's transitions, oldest first."""
    with conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "select exception_id::text, sequence, from_status, to_status, actor, reason, "
                "resolution_type, changed_at, run_id from {} where exception_id = %s "
                "order by sequence"
            ).format(sql.Identifier(schema, "exception_transitions")),
            (exception_id,),
        )
        return [TransitionRecord(*r) for r in cur.fetchall()]
