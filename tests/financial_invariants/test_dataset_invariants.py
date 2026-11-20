"""Phase 2 gate: the generated dev dataset satisfies every financial invariant.

Gate (roadmap): the simulator produces a fully balanced ledger, traceable
trades and positions, correct settlement obligations, and repeatable output.
Each check recomputes its expectation from the canonical events, independently
of the code under test.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

import pytest

from northstar.common.money import Currency
from northstar.domain.accounting import CLIENT_CASH, EntrySide
from northstar.domain.calendar import business_date
from northstar.domain.events import (
    DividendDeclared,
    DividendPaid,
    Event,
    ExerciseReason,
    ExpiryReason,
    InstrumentListed,
    OptionExercised,
    OptionExpired,
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


def trade_date_moves(events: Iterable[Event]) -> defaultdict[tuple[str, str], Decimal]:
    """INV-03's expected trade-date positions, recomputed from the events: executed
    quantities, plus exercises (the option out, the underlying in for a call or out
    for a put) and expiries (the option out)."""
    expected: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for e in events:
        match e:
            case TradeExecuted(payload=p):
                sign = 1 if p.side.value == "BUY" else -1
                expected[(p.account_id, p.instrument_id)] += sign * p.quantity
            case OptionExercised(payload=p):
                expected[(p.account_id, p.option_instrument_id)] -= p.contracts
                sign = 1 if p.right.value == "CALL" else -1
                expected[(p.account_id, p.underlying_instrument_id)] += sign * p.shares
            case OptionExpired(payload=p):
                expected[(p.account_id, p.instrument_id)] -= p.contracts
            case _:
                pass
    return expected


def test_inv03_positions_equal_cumulative_executed_quantity(events: tuple[Event, ...]) -> None:
    expected = trade_date_moves(events)
    assert any(isinstance(e, OptionExercised) for e in events)  # the dev dataset exercises
    assert any(isinstance(e, OptionExpired) for e in events)
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


def test_each_dividend_pays_the_holders_at_the_close_before_the_ex_date(
    events: tuple[Event, ...],
) -> None:
    # Under T+1 the shares settled at the end of the record (ex-)date are those held
    # at the close of the day before it (ADR 013), recomputed here from the trades.
    paid = [e for e in events if isinstance(e, DividendPaid)]
    declared = {e.payload.dividend_id for e in events if isinstance(e, DividendDeclared)}
    assert paid
    assert {e.payload.dividend_id for e in paid} == declared  # the dev dividends all have holders
    for e in paid:
        p = e.payload
        # Trades and exercises: a call exercised the day before settles on the record date.
        before_ex = trade_date_moves(
            t for t in events if business_date(t.event_timestamp) < p.record_date
        )
        held = {a: q for (a, i), q in before_ex.items() if i == p.instrument_id}
        assert {a.account_id: a.quantity for a in p.allocations} == {
            account: q for account, q in held.items() if q > 0
        }, p.dividend_id
        assert p.total_amount == sum(q * p.amount_per_share for q in held.values() if q > 0)


def test_every_option_is_gone_after_its_expiry(events: tuple[Event, ...]) -> None:
    expiries = {
        e.payload.instrument_id: e.payload.option.expiry
        for e in events
        if isinstance(e, InstrumentListed) and e.payload.option is not None
    }
    last = max(business_date(e.event_timestamp) for e in events)
    expired = {i for i, d in expiries.items() if d <= last}
    assert expired  # the dev dataset has options expiring in the window
    book = PositionBook().apply_all(events)
    assert not [k for k in book.positions() if k[1] in expired]


def test_exercises_happen_when_the_policy_or_the_model_says(events: tuple[Event, ...]) -> None:
    policy = policies().option_expiry
    terms = {
        e.payload.instrument_id: e.payload.option
        for e in events
        if isinstance(e, InstrumentListed) and e.payload.option is not None
    }
    reasons = Counter(e.payload.reason for e in events if isinstance(e, OptionExercised))
    assert reasons[ExerciseReason.EARLY] > 0  # the day before an ex-date, in the dev dataset
    assert reasons[ExerciseReason.EXPIRY] > 0
    for e in events:
        if isinstance(e, OptionExercised) and e.payload.reason is ExerciseReason.EXPIRY:
            p = e.payload
            assert p.underlying_close is not None
            assert policy.exercises_at_expiry(
                p.right, p.strike, p.underlying_close, p.exercise_date
            )
        if isinstance(e, OptionExpired):
            q, option = e.payload, terms[e.payload.instrument_id]
            in_the_money = policy.exercises_at_expiry(
                option.right, option.strike, q.underlying_close, q.expiry_date
            )
            assert in_the_money is (q.reason is ExpiryReason.NOT_EXERCISABLE)


def test_output_is_repeatable(events: tuple[Event, ...], tmp_path: Path) -> None:
    again = generate_dataset(load_policy(DEV, DatasetConfig), 42, policies())
    write_events(tmp_path / "a.jsonl", events)
    write_events(tmp_path / "b.jsonl", again)
    assert (tmp_path / "a.jsonl").read_bytes() == (tmp_path / "b.jsonl").read_bytes()
