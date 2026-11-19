"""INV-05: each client's cash equals the 2100 sub-ledger, and changes only via events.

The expected balance is computed independently of the posting engine: from
the events themselves, with the gross amount recomputed here.
"""

from collections import defaultdict
from decimal import ROUND_HALF_EVEN, Decimal

from hypothesis import given
from hypothesis import strategies as st

from northstar.common.money import Currency
from northstar.domain.accounting import CLIENT_CASH, EntrySide
from northstar.domain.events import CashDeposited, Side, TradeExecuted, TradeSettled

from factories import (
    commissions,
    covered_timestamps,
    deposit,
    engine,
    execution,
    positive_amounts,
    settlement,
)

ACCOUNTS = ("acc_s1_000001", "acc_s1_000002", "acc_s1_000003")
CENT = Decimal("0.01")


@st.composite
def client_events(draw: st.DrawFn) -> list[CashDeposited | TradeExecuted | TradeSettled]:
    events: list[CashDeposited | TradeExecuted | TradeSettled] = []
    for n in range(draw(st.integers(min_value=1, max_value=25))):
        account = draw(st.sampled_from(ACCOUNTS))
        when = draw(covered_timestamps())
        event_id = f"evt_s1_{n + 1:06d}"
        kind = draw(st.sampled_from(("deposit", "buy", "sell", "settle")))
        if kind == "deposit":
            events.append(
                deposit(
                    amount=draw(positive_amounts),
                    currency=Currency.CAD,
                    account_id=account,
                    when=when,
                    event_id=event_id,
                )
            )
        elif kind == "settle":
            events.append(
                settlement(
                    amount=draw(positive_amounts),
                    currency=Currency.CAD,
                    when=when,
                    event_id=event_id,
                )
            )
        else:
            quantity = str(draw(st.integers(min_value=1, max_value=5000)))
            price = format(
                draw(st.decimals(min_value=Decimal("0.01"), max_value=Decimal(1000), places=4)), "f"
            )
            events.append(
                execution(
                    side=Side.BUY if kind == "buy" else Side.SELL,
                    quantity=quantity,
                    price=price,
                    commission=draw(commissions),
                    currency=Currency.CAD,
                    account_id=account,
                    when=when,
                    event_id=event_id,
                    execution_id=f"exe_s1_{n + 1:06d}",
                )
            )
    return events


def expected_client_cash(
    events: list[CashDeposited | TradeExecuted | TradeSettled],
) -> dict[str, Decimal]:
    balance: dict[str, Decimal] = defaultdict(Decimal)
    for event in events:
        match event:
            case CashDeposited(payload=p):
                balance[p.account_id] += p.amount
            case TradeExecuted(payload=p):
                gross = (p.quantity * p.price * p.multiplier).quantize(
                    CENT, rounding=ROUND_HALF_EVEN
                )
                if p.side is Side.BUY:
                    balance[p.account_id] -= gross + p.commission
                else:
                    balance[p.account_id] += gross - p.commission
            case TradeSettled():
                pass  # settlement moves firm cash and clearing balances, never client cash
    return dict(balance)


@given(events=client_events())
def test_client_cash_equals_the_2100_subledger(
    events: list[CashDeposited | TradeExecuted | TradeSettled],
) -> None:
    ledger: dict[str, Decimal] = defaultdict(Decimal)
    for event in events:
        for line in engine().post(event).lines:
            if line.gl_account == CLIENT_CASH:
                assert line.client_account_id is not None
                signed = (
                    line.amount.amount if line.side is EntrySide.CREDIT else -line.amount.amount
                )
                ledger[line.client_account_id] += signed  # 2100 is a liability: credit = money held
    expected = expected_client_cash(events)
    for account in ACCOUNTS:
        assert ledger.get(account, Decimal(0)) == expected.get(account, Decimal(0))
