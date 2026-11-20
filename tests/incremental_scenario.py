"""Late records arrive in rounds; incremental dbt builds must equal a full refresh.

Shared by the Postgres integration test and the live Redshift test (roadmap
4F-b). The seed-42 dev dataset is loaded with some of its own events held
back. They then arrive late, in rounds, each dated later than the one before:
a sale that closes a position for good, a deposit, a price, an FX rate, and an
account update. Each round is posted by Python like any other load and built
incrementally. A round must rewrite exactly the rows from its date onward in
the daily models it can affect, and nothing in the others. Because each round
is later than the last, a rebuild a round missed would still be wrong at the
end, where every model must equal a ``--full-refresh`` build, row for row.
Without the ``rebuild`` strategy, the closed position's rows would survive. A
rerun with no new data must write nothing.
"""

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
from psycopg import sql

from northstar.application.policies import load_policies
from northstar.application.reference_data import calendar_days
from northstar.domain.accounting import PostingEngine
from northstar.domain.calendar import BUSINESS_TIMEZONE
from northstar.domain.events import (
    AccountUpdated,
    CashDeposited,
    Event,
    FxRatePublished,
    OrderCancelled,
    OrderPlaced,
    PriceUpdated,
    Side,
    TradeExecuted,
    TradeSettled,
)
from northstar.infrastructure.config_loader import load_policy
from northstar.infrastructure.postgres import (
    WarehouseSchemas,
    ensure_tables,
    load_calendar_days,
    load_events,
    load_journal,
)
from northstar.infrastructure.recon_store import ensure_recon_tables
from northstar.simulation.engine import LEDGER_CALENDAR_MARKET
from northstar.simulation.generator import DatasetConfig, generate_dataset

from factories import CONFIG

ROOT = Path(__file__).parents[1]
DBT_PROJECT = ROOT / "warehouse" / "dbt"
DBT = Path(sys.executable).parent / "dbt"
DATASET = CONFIG / "simulation" / "datasets" / "dev.yaml"
# Late records come from this date on (mid-window, after a month of history).
LATE_FROM = date(2026, 2, 9)
DAILY_MODELS = ("fct_positions_daily", "mart_daily_client_cash", "mart_daily_positions")
POSITIONS = frozenset({"fct_positions_daily", "mart_daily_positions"})


@dataclass(frozen=True, slots=True)
class Round:
    """Records that arrive late together, and the daily models they must rebuild."""

    name: str
    events: tuple[Event, ...]
    from_date: date
    rebuilds: frozenset[str]


@dataclass(frozen=True, slots=True)
class Scenario:
    """The dataset split into what arrives on time and the late rounds."""

    on_time: tuple[Event, ...]
    rounds: tuple[Round, ...]
    closed_account: str
    closed_instrument: str
    closed_settles_on: date
    updated_account: str


@dataclass(frozen=True, slots=True)
class BuildSchemas:
    """Throwaway schemas: the loaded data, an incremental build, and a full-refresh build."""

    warehouse: WarehouseSchemas
    incremental: str
    full: str


def _effective_date(event: Event) -> date:
    """The first business date the event can change (as int_arrivals defines it)."""
    if isinstance(event, PriceUpdated):
        return event.payload.price_date
    if isinstance(event, FxRatePublished):
        return event.payload.rate_date
    if isinstance(event, TradeExecuted):
        return event.payload.trade_date
    return event.event_timestamp.astimezone(BUSINESS_TIMEZONE).date()


def _closing_sale(events: Sequence[Event]) -> TradeExecuted:
    """The first execution from LATE_FROM on that closes a position for good."""
    holdings: dict[tuple[str, str], Decimal] = {}
    closes: dict[tuple[str, str], TradeExecuted] = {}
    for event in events:
        if isinstance(event, TradeExecuted):
            p = event.payload
            key = (p.account_id, p.instrument_id)
            holdings[key] = holdings.get(key, Decimal(0)) + (
                p.quantity if p.side is Side.BUY else -p.quantity
            )
            if holdings[key] == 0:
                closes[key] = event
            else:
                closes.pop(key, None)  # reopened: not closed for good
    return min(
        (e for e in closes.values() if e.payload.trade_date >= LATE_FROM),
        key=lambda e: (e.event_timestamp, e.event_id),
    )


def _scenario() -> Scenario:
    """Hold back five rounds of the dataset's own events, each later than the last."""
    events = sorted(
        generate_dataset(load_policy(DATASET, DatasetConfig), 42, load_policies(CONFIG)),
        key=lambda e: (e.event_timestamp, e.event_id),
    )
    sale = _closing_sale(events)
    order_id = sale.payload.order_id
    settlements = {
        e.payload.settlement_id
        for e in events
        if isinstance(e, TradeExecuted) and e.payload.order_id == order_id
    }
    order = tuple(
        e
        for e in events
        if (
            isinstance(e, OrderPlaced | OrderCancelled | TradeExecuted)
            and e.payload.order_id == order_id
        )
        or (isinstance(e, TradeSettled) and e.payload.settlement_id in settlements)
    )
    rounds = [Round("sale", order, min(map(_effective_date, order)), POSITIONS | {DAILY_MODELS[1]})]
    # Each later round starts a week after the previous one, so no round's dates hide another's.
    for kind, rebuilds in (
        (CashDeposited, POSITIONS | {DAILY_MODELS[1]}),  # journal lines extend reporting dates
        (PriceUpdated, POSITIONS),
        (FxRatePublished, POSITIONS | {DAILY_MODELS[1]}),
    ):
        after = rounds[-1].from_date + timedelta(days=7)
        event = next(e for e in events if isinstance(e, kind) and _effective_date(e) >= after)
        rounds.append(Round(kind.__name__, (event,), _effective_date(event), frozenset(rebuilds)))
    update = next(e for e in events if isinstance(e, AccountUpdated))
    rounds.append(Round("account update", (update,), _effective_date(update), frozenset()))
    held = {e.event_id for r in rounds for e in r.events}
    return Scenario(
        on_time=tuple(e for e in events if e.event_id not in held),
        rounds=tuple(rounds),
        closed_account=sale.payload.account_id,
        closed_instrument=sale.payload.instrument_id,
        closed_settles_on=sale.payload.settlement_date,
        updated_account=update.payload.account_id,
    )


def _load(
    conn: psycopg.Connection[Any], events: Sequence[Event], run_id: str, schemas: WarehouseSchemas
) -> None:
    """Post and load events the way ``load-run`` does, without the object store."""
    policies = load_policies(CONFIG)
    batches = PostingEngine(policies.chart, policies.calendars[LEDGER_CALENDAR_MARKET]).post_events(
        events
    )
    with conn.transaction():
        ensure_tables(conn, schemas)
        load_events(conn, events, run_id, schemas)
        load_journal(conn, batches, run_id, schemas)
        load_calendar_days(conn, calendar_days(policies.calendars), run_id, schemas)
        ensure_recon_tables(conn, schemas.recon)


def _dbt_run(schemas: BuildSchemas, warehouse: str, schema: str, work: Path, *flags: str) -> str:
    """``dbt run`` on ``warehouse`` into ``schema``; return the build's invocation ID."""
    target = work / f"target-{uuid.uuid4().hex[:8]}"
    variables = json.dumps(
        {
            "raw_schema": schemas.warehouse.raw,
            "ledger_schema": schemas.warehouse.ledger,
            "recon_schema": schemas.warehouse.recon,
        }
    )
    result = subprocess.run(  # noqa: S603 - fixed arguments, no shell
        [
            str(DBT),
            "run",
            *flags,
            "--vars",
            variables,
            "--target-path",
            str(target),
            "--log-path",
            str(work / "logs"),
        ],
        cwd=DBT_PROJECT,
        env={
            **os.environ,
            "NBI_DBT_SCHEMA": schema,
            "NBI_DBT_TARGET": "redshift" if warehouse == "redshift" else "local",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-3000:]
    results = json.loads((target / "run_results.json").read_text())
    return str(results["metadata"]["invocation_id"])


def _incremental_models(conn: psycopg.Connection[Any], schema: str) -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            sql.SQL("select distinct model_name from {} order by 1").format(
                sql.Identifier(schema, "dbt_model_builds")
            )
        ).fetchall()
    ]


def _rows_written(conn: psycopg.Connection[Any], schema: str, model: str, invocation: str) -> int:
    if model == "int_arrivals":
        query = sql.SQL(
            "select count(*) from {} where _build_seq = "
            "(select build_seq from {} where invocation_id = %s)"
        ).format(sql.Identifier(schema, model), sql.Identifier(schema, "dbt_build_log"))
    else:
        query = sql.SQL("select count(*) from {} where _dbt_invocation_id = %s").format(
            sql.Identifier(schema, model)
        )
    row = conn.execute(query, (invocation,)).fetchone()
    assert row is not None
    return int(row[0])


def _count(conn: psycopg.Connection[Any], query: sql.Composed, *params: object) -> int:
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return int(row[0])


def _compare_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "compare_warehouses", ROOT / "scripts" / "compare_warehouses.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_round(
    conn: psycopg.Connection[Any], schema: str, late: Round, invocation: str
) -> dict[str, int]:
    """Assert the round rewrote exactly its dates in the daily models it affects, and nothing
    in the others; return the rows it wrote per incremental model."""
    written = {
        m: _rows_written(conn, schema, m, invocation) for m in _incremental_models(conn, schema)
    }
    for model in DAILY_MODELS:
        if model not in late.rebuilds:
            assert written[model] == 0, (late.name, model)
            continue
        table = sql.Identifier(schema, model)
        rebuilt_from = conn.execute(
            sql.SQL("select min(business_date) from {} where _dbt_invocation_id = %s").format(
                table
            ),
            (invocation,),
        ).fetchone()
        assert rebuilt_from is not None
        assert rebuilt_from[0] is not None, (late.name, model)
        assert rebuilt_from[0] >= late.from_date, (late.name, model)
        suffix = _count(
            conn,
            sql.SQL("select count(*) from {} where business_date >= %s").format(table),
            rebuilt_from[0],
        )
        total = _count(conn, sql.SQL("select count(*) from {}").format(table))
        assert 0 < written[model] == suffix < total, (late.name, model)
    return written


def assert_late_rounds_build_like_a_full_refresh(
    conn: psycopg.Connection[Any], warehouse: str, schemas: BuildSchemas, work: Path
) -> None:
    """Run the scenario on ``warehouse`` (``postgres`` or ``redshift``) and assert it.

    ``conn`` must be in autocommit mode, so each load commits before dbt (another
    process) reads it: a read would otherwise leave a transaction open and turn
    the next load into a savepoint.
    """
    scenario = _scenario()
    dates = [r.from_date for r in scenario.rounds[:-1]]
    assert dates == sorted(set(dates))
    assert dates[0] >= LATE_FROM

    _load(conn, scenario.on_time, "on-time", schemas.warehouse)
    _dbt_run(schemas, warehouse, schemas.incremental, work)
    closed_rows = sql.SQL(
        "select count(*) from {} where account_id = %s and instrument_id = %s "
        "and business_date >= %s"
    ).format(sql.Identifier(schemas.incremental, "fct_positions_daily"))
    closed = (scenario.closed_account, scenario.closed_instrument, scenario.closed_settles_on)
    # Without the sale, the position is still open after its settlement date.
    assert _count(conn, closed_rows, *closed) > 0

    for late in scenario.rounds:
        _load(conn, late.events, f"late-{late.name}", schemas.warehouse)
        written = _check_round(
            conn, schemas.incremental, late, _dbt_run(schemas, warehouse, schemas.incremental, work)
        )
        if late.name == "sale":
            # The sale closed the position: its rows from the settlement date are gone.
            assert _count(conn, closed_rows, *closed) == 0
            assert written["fct_orders"] == 1
            assert written["fct_executions"] == sum(
                isinstance(e, TradeExecuted) for e in late.events
            )
        if late.name == "account update":
            versions = _count(
                conn,
                sql.SQL("select count(*) from {} where account_id = %s").format(
                    sql.Identifier(schemas.incremental, "dim_account")
                ),
                scenario.updated_account,
            )
            assert written["dim_account"] == versions == 2
            assert written["fct_orders"] == 0

    _dbt_run(schemas, warehouse, schemas.full, work, "--full-refresh")
    differences = _compare_module().compare(
        schemas.incremental, left=warehouse, right=warehouse, right_schema=schemas.full
    )
    assert differences == []

    # SCD2: the late update closed the account's opening version.
    chain = conn.execute(
        sql.SQL(
            "select change_reason, valid_to is null, is_current from {} "
            "where account_id = %s order by valid_from"
        ).format(sql.Identifier(schemas.incremental, "dim_account")),
        (scenario.updated_account,),
    ).fetchall()
    assert chain[0] == ("ACCOUNT_OPENED", False, False)
    assert chain[-1][1:] == (True, True)

    # A rerun with no new data writes nothing.
    rerun = _dbt_run(schemas, warehouse, schemas.incremental, work)
    models = _incremental_models(conn, schemas.incremental)
    assert {m: _rows_written(conn, schemas.incremental, m, rerun) for m in models} == dict.fromkeys(
        models, 0
    )
