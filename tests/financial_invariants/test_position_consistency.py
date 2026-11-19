"""INV-03 (position = cumulative executed quantity) and INV-04 (stock record balances).

Event streams are generated as random buys, sells within the position, and
settlements of each execution, applied in time order.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from northstar.domain.events import Side, TradeExecuted, TradeSettled
from northstar.domain.positions import PositionBook, SettlementError, ShortSaleError

from factories import at_toronto, execution, settlement

ACCOUNTS = ("acc_s1_000001", "acc_s1_000002")
INSTRUMENTS = ("ins_s1_000001", "ins_s1_000002")


def _with(event: TradeExecuted, account: str, instrument: str) -> TradeExecuted:
    return event.model_copy(
        update={
            "payload": event.payload.model_copy(
                update={"account_id": account, "instrument_id": instrument}
            )
        }
    )


def _settles(execution_event: TradeExecuted, event_id: str) -> TradeSettled:
    p = execution_event.payload
    base = settlement(side=p.side, amount="1.00", event_id=event_id)
    return base.model_copy(
        update={
            "payload": base.payload.model_copy(
                update={
                    "account_id": p.account_id,
                    "instrument_id": p.instrument_id,
                    "quantity": p.quantity,
                    "execution_ids": (p.execution_id,),
                }
            )
        }
    )


@st.composite
def streams(draw: st.DrawFn) -> list[TradeExecuted | TradeSettled]:
    held: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
    events: list[TradeExecuted | TradeSettled] = []
    unsettled: list[TradeExecuted] = []
    day = date(2026, 3, 2)
    for n in range(draw(st.integers(min_value=1, max_value=30))):
        account, instrument = draw(st.sampled_from(ACCOUNTS)), draw(st.sampled_from(INSTRUMENTS))
        key = (account, instrument)
        can_sell = held[key] > 0
        side = draw(st.sampled_from([Side.BUY, Side.SELL] if can_sell else [Side.BUY]))
        if side is Side.BUY:
            qty = Decimal(draw(st.integers(min_value=1, max_value=500)))
            held[key] += qty
        else:
            qty = Decimal(draw(st.integers(min_value=1, max_value=int(held[key]))))
            held[key] -= qty
        event = _with(
            execution(
                side=side,
                quantity=str(qty),
                when=at_toronto(day, 10, n % 60),
                event_id=f"evt_e{n:04d}",
                execution_id=f"exe_{n:04d}",
            ),
            account,
            instrument,
        )
        events.append(event)
        unsettled.append(event)
        if draw(st.booleans()):  # settle some earlier executions now
            events.extend(_settles(p, f"evt_s{p.payload.execution_id}") for p in unsettled)
            unsettled.clear()
        day += timedelta(days=1)
    return events


@given(stream=streams())
def test_inv03_positions_equal_cumulative_executed_quantity(
    stream: list[TradeExecuted | TradeSettled],
) -> None:
    book = PositionBook()
    expected_trade_date: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
    expected_settled: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for event in stream:
        book.apply(event)
        p = event.payload
        sign = 1 if p.side is Side.BUY else -1
        key = (p.account_id, p.instrument_id)
        if isinstance(event, TradeExecuted):
            expected_trade_date[key] += sign * p.quantity
        else:
            expected_settled[key] += sign * p.quantity
        assert book.trade_date_quantity(*key) == expected_trade_date[key]
        assert book.settled_quantity(*key) == expected_settled[key]
        assert book.trade_date_quantity(*key) >= 0  # long only


@given(stream=streams())
def test_inv04_stock_record_balances_after_every_event(
    stream: list[TradeExecuted | TradeSettled],
) -> None:
    book = PositionBook()
    for event in stream:
        book.apply(event)
        for line in book.stock_record():
            assert line.balanced, line
            assert min(line.pending_delivery, line.pending_receipt, line.depository) >= 0


def test_stock_record_walkthrough_buy_then_settle() -> None:
    # Domain model §9.3: BUY 10 on T, settle T+1.
    buy = execution(side=Side.BUY, quantity="10")
    book = PositionBook().apply_all([buy])
    (line,) = book.stock_record()
    assert (line.client_ownership, line.pending_receipt, line.depository) == (
        Decimal(10),
        Decimal(10),
        Decimal(0),
    )
    book.apply(_settles(buy, "evt_settle"))
    (line,) = book.stock_record()
    assert (line.client_ownership, line.pending_receipt, line.depository) == (
        Decimal(10),
        Decimal(0),
        Decimal(10),
    )
    assert book.settled_quantity("acc_s1_000001", "ins_s1_000001") == 10


def test_short_sales_are_refused() -> None:
    book = PositionBook().apply_all([execution(side=Side.BUY, quantity="5")])
    with pytest.raises(ShortSaleError, match="short selling is out of scope"):
        book.apply(execution(side=Side.SELL, quantity="6", execution_id="exe_s1_000002"))


def test_settling_more_than_in_flight_is_refused() -> None:
    buy = execution(side=Side.BUY, quantity="5")
    too_much = _settles(buy, "evt_x").model_copy(
        update={
            "payload": _settles(buy, "evt_x").payload.model_copy(update={"quantity": Decimal(6)})
        }
    )
    with pytest.raises(SettlementError, match="only 5 is pending"):
        PositionBook().apply_all([buy, too_much])
