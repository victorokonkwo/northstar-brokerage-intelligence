"""Phase 2 gate: the generated dev dataset satisfies every financial invariant.

Gate (roadmap): the simulator produces a fully balanced ledger, traceable
trades and positions, correct settlement obligations, and repeatable output.
Each check recomputes its expectation from the canonical events, independently
of the code under test.
"""

from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

import pytest

from northstar.common.money import Currency
from northstar.domain.accounting import CLIENT_CASH, EntrySide
from northstar.domain.events import (
    Event,
    InstrumentListed,
    OrderCancelled,
    OrderPlaced,
    TradeExecuted,
    TradeSettled,
)
from northstar.domain.positions import PositionBook
from northstar.domain.settlement import settlement_date
from northstar.infrastructure.config_loader import load_policy
from northstar.infrastructure.event_files import write_events
from northstar.simulation.generator import DatasetConfig, generate_dataset

from factories import CONFIG, engine, expected_client_cash, policies

DEV = CONFIG / "simulation" / "datasets" / "dev.yaml"


@pytest.fixture(scope="module")
def events() -> tuple[Event, ...]:
    return generate_dataset(load_policy(DEV, DatasetConfig), 42, policies())


def test_inv01_every_batch_balances_per_currency(events: tuple[Event, ...]) -> None:
    batches = engine().post_events(events)
    assert len(batches) > 1000
    for batch in batches:
        for debits, credits in batch.totals().values():
            assert debits == credits


def test_inv03_positions_equal_cumulative_executed_quantity(events: tuple[Event, ...]) -> None:
    expected: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for e in events:
        if isinstance(e, TradeExecuted):
            sign = 1 if e.payload.side.value == "BUY" else -1
            expected[(e.payload.account_id, e.payload.instrument_id)] += sign * e.payload.quantity
    book = PositionBook().apply_all(events)
    for key, quantity in expected.items():
        assert book.trade_date_quantity(*key) == quantity
        assert quantity >= 0  # long only


def test_inv04_stock_record_balances_after_every_event(events: tuple[Event, ...]) -> None:
    book = PositionBook()
    for e in events:
        book.apply(e)
        if isinstance(e, TradeExecuted | TradeSettled):
            assert all(line.balanced for line in book.stock_record())


def test_inv05_client_cash_equals_the_2100_subledger(events: tuple[Event, ...]) -> None:
    ledger: defaultdict[tuple[str, Currency], Decimal] = defaultdict(Decimal)
    for batch in engine().post_events(events):
        for line in batch.lines:
            if line.gl_account == CLIENT_CASH:
                assert line.client_account_id is not None
                signed = (
                    line.amount.amount if line.side is EntrySide.CREDIT else -line.amount.amount
                )
                ledger[(line.client_account_id, line.amount.currency)] += signed
    expected = expected_client_cash(events)
    for key in set(ledger) | set(expected):
        assert ledger.get(key, Decimal(0)) == expected.get(key, Decimal(0)), key


def test_inv06_every_execution_settles_in_exactly_one_obligation(events: tuple[Event, ...]) -> None:
    executions = {e.payload.execution_id: e.payload for e in events if isinstance(e, TradeExecuted)}
    settlements = [e.payload for e in events if isinstance(e, TradeSettled)]
    membership = Counter(eid for s in settlements for eid in s.execution_ids)
    assert set(membership) == set(executions)
    assert set(membership.values()) == {1}
    for s in settlements:
        members = [executions[eid] for eid in s.execution_ids]
        assert {(m.order_id, m.trade_date, m.settlement_id) for m in members} == {
            (s.order_id, s.trade_date, s.settlement_id)
        }
        assert s.amount == sum(
            (m.quantity * m.price * m.multiplier).quantize(Decimal("0.01")) for m in members
        )
        assert s.quantity == sum(m.quantity for m in members)


def test_trades_are_traceable_to_orders(events: tuple[Event, ...]) -> None:
    orders = {e.payload.order_id: e.payload for e in events if isinstance(e, OrderPlaced)}
    cancelled = {
        e.payload.order_id: e.payload.cancelled_quantity
        for e in events
        if isinstance(e, OrderCancelled)
    }
    filled: defaultdict[str, Decimal] = defaultdict(Decimal)
    order_events = {e.payload.order_id: e.event_id for e in events if isinstance(e, OrderPlaced)}
    for e in events:
        if isinstance(e, TradeExecuted):
            o = orders[e.payload.order_id]
            assert (o.account_id, o.instrument_id, o.side) == (
                e.payload.account_id,
                e.payload.instrument_id,
                e.payload.side,
            )
            assert e.causation_id == order_events[e.payload.order_id]
            filled[e.payload.order_id] += e.payload.quantity
    for order_id, o in orders.items():
        assert filled[order_id] + cancelled.get(order_id, Decimal(0)) <= o.quantity
        if order_id in cancelled:
            assert filled[order_id] + cancelled[order_id] == o.quantity


def test_settlement_dates_follow_the_calendars(events: tuple[Event, ...]) -> None:
    p = policies()
    listed = {e.payload.instrument_id: e.payload for e in events if isinstance(e, InstrumentListed)}
    for e in events:
        if isinstance(e, TradeExecuted):
            calendar = p.calendars[e.payload.market]
            assert calendar.is_trading_day(e.payload.trade_date)
            assert e.payload.asset_class is listed[e.payload.instrument_id].asset_class
            assert e.payload.settlement_date == settlement_date(
                e.payload.trade_date,
                e.payload.market,
                e.payload.asset_class,
                p.settlement_rules,
                calendar,
            )


def test_output_is_repeatable(events: tuple[Event, ...], tmp_path: Path) -> None:
    again = generate_dataset(load_policy(DEV, DatasetConfig), 42, policies())
    write_events(tmp_path / "a.jsonl", events)
    write_events(tmp_path / "b.jsonl", again)
    assert (tmp_path / "a.jsonl").read_bytes() == (tmp_path / "b.jsonl").read_bytes()
