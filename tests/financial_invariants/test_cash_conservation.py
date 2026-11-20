"""INV-05: each client's cash equals the 2100 sub-ledger, and changes only via events.

The expected balance is computed independently of the posting engine: from
the events themselves, with the gross amount recomputed here.
"""

from collections import defaultdict
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from northstar.common.money import Currency, Money
from northstar.domain.accounting import CLIENT_CASH, EntrySide
from northstar.domain.events import (
    CashDeposited,
    CashWithdrawn,
    DividendPaid,
    ExerciseSettled,
    FxConverted,
    MarginInterestAccrued,
    OptionExercised,
    OptionRight,
    Side,
    TradeExecuted,
    TradeSettled,
)
from northstar.domain.fx import quote

from factories import (
    commissions,
    covered_timestamps,
    deposit,
    dividend_paid,
    engine,
    execution,
    exercise_settled,
    expected_client_cash,
    fx_conversion,
    interest,
    option_exercised,
    positive_amounts,
    settlement,
    withdrawal,
)

type ClientEvent = (
    CashDeposited
    | CashWithdrawn
    | FxConverted
    | MarginInterestAccrued
    | TradeExecuted
    | TradeSettled
    | DividendPaid
    | OptionExercised
    | ExerciseSettled
)

ACCOUNTS = ("acc_s1_000001", "acc_s1_000002", "acc_s1_000003")
CENT = Decimal("0.01")


@st.composite
def client_events(draw: st.DrawFn) -> list[ClientEvent]:
    events: list[ClientEvent] = []
    for n in range(draw(st.integers(min_value=1, max_value=25))):
        account = draw(st.sampled_from(ACCOUNTS))
        when = draw(covered_timestamps())
        event_id = f"evt_s1_{n + 1:06d}"
        kind = draw(
            st.sampled_from(
                (
                    "deposit",
                    "withdraw",
                    "fx",
                    "interest",
                    "buy",
                    "sell",
                    "settle",
                    "dividend",
                    "exercise",
                )
            )
        )
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
        elif kind == "withdraw":
            events.append(
                withdrawal(
                    amount=draw(positive_amounts),
                    currency=draw(st.sampled_from(Currency)),
                    account_id=account,
                    when=when,
                    event_id=event_id,
                )
            )
        elif kind == "fx":
            to_usd = draw(st.booleans())
            sell_ccy = Currency.CAD if to_usd else Currency.USD
            q = quote(
                Money(Decimal(draw(st.integers(1000, 10**9))).scaleb(-2), sell_ccy),
                Currency.USD if to_usd else Currency.CAD,
                Decimal(draw(st.sampled_from(["1.3512340000", "1.3700000000", "1.4021000000"]))),
                Decimal("0.015"),
            )
            events.append(
                fx_conversion(
                    str(q.sell.amount),
                    sell_ccy,
                    str(q.buy.amount),
                    reference_rate=str(q.reference_rate),
                    client_rate=str(q.client_rate),
                    account_id=account,
                    when=when,
                    event_id=event_id,
                )
            )
        elif kind == "interest":
            events.append(
                interest(
                    amount=draw(st.sampled_from(["0.01", "1.64", "8.22"])),
                    currency=draw(st.sampled_from(Currency)),
                    account_id=account,
                    event_id=event_id,
                )
            )
        elif kind == "exercise":
            exercised = option_exercised(
                draw(st.sampled_from(OptionRight)),
                str(draw(st.integers(1, 50))),
                str(Decimal(draw(st.integers(1, 10**5))).scaleb(-2)),
                draw(st.sampled_from(Currency)),
                account_id=account,
                when=when,
                event_id=event_id,
            )
            events.extend([exercised, exercise_settled(exercised, f"{event_id}s")])
        elif kind == "dividend":
            holders = draw(st.lists(st.sampled_from(ACCOUNTS), min_size=1, max_size=3, unique=True))
            events.append(
                dividend_paid(
                    holdings=[(a, str(draw(st.integers(1, 10**6)))) for a in holders],
                    rate=draw(st.sampled_from(["0.01", "0.25", "1.37"])),
                    currency=draw(st.sampled_from(Currency)),
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


@given(events=client_events())
def test_client_cash_equals_the_2100_subledger(
    events: list[ClientEvent],
) -> None:
    ledger: dict[tuple[str, Currency], Decimal] = defaultdict(Decimal)
    for event in events:
        for line in engine().post(event).lines:
            if line.gl_account == CLIENT_CASH:
                assert line.client_account_id is not None
                signed = (
                    line.amount.amount if line.side is EntrySide.CREDIT else -line.amount.amount
                )
                # 2100 is a liability: a credit is money held, a debit balance is a loan.
                ledger[(line.client_account_id, line.amount.currency)] += signed
    expected = expected_client_cash(events)
    for key in set(ledger) | set(expected):
        assert ledger.get(key, Decimal(0)) == expected.get(key, Decimal(0)), key
