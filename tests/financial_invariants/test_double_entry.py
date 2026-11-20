"""INV-01: every accepted journal batch balances per currency (domain model §8).

Property tests over generated events for every v1 posting rule. KPI-01 counts
the batches that would violate this; it must be zero.
"""

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from northstar.common.exceptions import UnbalancedJournalError
from northstar.common.money import Currency, Money
from northstar.domain.accounting import EntrySide, JournalBatch
from northstar.domain.events import OptionRight, Side
from northstar.domain.fx import quote
from northstar.domain.interest import daily_interest

from factories import (
    commissions,
    covered_timestamps,
    currencies,
    deposit,
    dividend_paid,
    engine,
    execution,
    exercise_settled,
    fx_conversion,
    interest,
    option_exercised,
    positive_amounts,
    prices,
    quantities,
    settlement,
    sides,
    withdrawal,
)


def assert_balanced(batch: JournalBatch) -> None:
    for debits, credits in batch.totals().values():
        assert debits == credits
    for line in batch.lines:
        assert line.amount.amount > 0
        assert line.amount.is_quantized


@given(
    holdings=st.lists(st.integers(1, 10**7), min_size=1, max_size=20),
    rate=st.integers(1, 10**4).map(lambda cents: str(Decimal(cents).scaleb(-2))),
    currency=currencies,
    when=covered_timestamps(),
)
def test_dividends_balance(
    holdings: list[int], rate: str, currency: Currency, when: datetime
) -> None:
    accounts = [(f"acc_s1_{n:06d}", str(q)) for n, q in enumerate(holdings, start=1)]
    batch = engine().post(dividend_paid(accounts, rate, currency, when))
    assert_balanced(batch)
    cash = [line for line in batch.lines if line.gl_account == "1000"]
    assert len(cash) == 1  # one bank line per dividend (ADR 013)


@given(
    right=st.sampled_from(OptionRight),
    contracts=st.integers(1, 10**4),
    strike_cents=st.integers(1, 10**6),
    currency=currencies,
    when=covered_timestamps(),
)
def test_exercises_and_their_settlements_balance(
    right: OptionRight, contracts: int, strike_cents: int, currency: Currency, when: datetime
) -> None:
    strike = str(Decimal(strike_cents).scaleb(-2))
    event = option_exercised(right, str(contracts), strike, currency, when=when)
    assert_balanced(engine().post(event))
    assert_balanced(engine().post(exercise_settled(event)))


@given(amount=positive_amounts, currency=currencies, when=covered_timestamps())
def test_deposits_balance(amount: str, currency: Currency, when: datetime) -> None:
    assert_balanced(engine().post(deposit(amount=amount, currency=currency, when=when)))


@given(
    side=sides,
    quantity=quantities,
    price=prices,
    commission=commissions,
    currency=currencies,
    when=covered_timestamps(),
)
def test_executions_balance(
    side: Side, quantity: str, price: str, commission: str, currency: Currency, when: datetime
) -> None:
    gross = (Decimal(quantity) * Decimal(price)).quantize(Decimal("0.01"))
    # Gross and commission both zero is a zero-value event: nothing to post
    # (see test_zero_value_execution_is_rejected). Gross alone may round to 0.
    assume(gross != 0 or Decimal(commission) != 0)
    batch = engine().post(
        execution(
            side=side,
            quantity=quantity,
            price=price,
            commission=commission,
            currency=currency,
            when=when,
        )
    )
    assert_balanced(batch)


@given(side=sides, amount=positive_amounts, currency=currencies, when=covered_timestamps())
def test_settlements_balance(side: Side, amount: str, currency: Currency, when: datetime) -> None:
    assert_balanced(
        engine().post(settlement(side=side, amount=amount, currency=currency, when=when))
    )


@given(
    quantity=quantities,
    price=prices,
    commission=commissions,
    which=st.integers(min_value=0, max_value=10),
)
def test_shifting_any_line_by_one_cent_is_rejected(
    quantity: str, price: str, commission: str, which: int
) -> None:
    gross = (Decimal(quantity) * Decimal(price)).quantize(Decimal("0.01"))
    assume(gross != 0 or Decimal(commission) != 0)
    batch = engine().post(execution(quantity=quantity, price=price, commission=commission))
    index = which % len(batch.lines)
    target = batch.lines[index]
    shifted = replace(target, amount=target.amount + Money(Decimal("0.01"), target.amount.currency))
    with pytest.raises(UnbalancedJournalError):
        replace(batch, lines=(*batch.lines[:index], shifted, *batch.lines[index + 1 :]))


def test_debit_total_of_a_buy_is_gross_plus_commission() -> None:
    batch = engine().post(execution(quantity="3", price="33.333333", commission="4.95"))
    debits, credits = batch.totals()[Currency.USD]
    assert debits.amount == Decimal("104.95")  # 99.999999 rounds to 100.00
    assert [line.side for line in batch.lines] == [
        EntrySide.DEBIT,
        EntrySide.CREDIT,
        EntrySide.CREDIT,
    ]
    assert credits == debits


def test_zero_value_execution_is_rejected() -> None:
    # 1 share at 0.004 rounds to 0.00 and no commission is charged: there is
    # no monetary effect, so no batch can be formed.
    with pytest.raises(ValueError, match="at least two lines"):
        engine().post(execution(quantity="1", price="0.004", commission="0.00"))


# --- Rules added in Phase 2 -------------------------------------------------


@given(amount=positive_amounts, currency=currencies, when=covered_timestamps())
def test_withdrawals_balance(amount: str, currency: Currency, when: datetime) -> None:
    assert_balanced(engine().post(withdrawal(amount=amount, currency=currency, when=when)))


@given(
    cents=st.integers(min_value=1000, max_value=10**11),
    to_usd=st.booleans(),
    ref=st.decimals(min_value=Decimal("1.2"), max_value=Decimal("1.5"), places=8),
    when=covered_timestamps(),
)
def test_fx_conversions_balance_in_both_currencies(
    cents: int, to_usd: bool, ref: Decimal, when: datetime
) -> None:
    sell_currency = Currency.CAD if to_usd else Currency.USD
    q = quote(
        Money(Decimal(cents).scaleb(-2), sell_currency),
        Currency.USD if to_usd else Currency.CAD,
        ref,
        Decimal("0.015"),
    )
    event = fx_conversion(
        str(q.sell.amount),
        sell_currency,
        str(q.buy.amount),
        reference_rate=str(q.reference_rate),
        client_rate=str(q.client_rate),
        when=when,
    )
    batch = engine().post(event)
    assert_balanced(batch)
    assert set(batch.totals()) == {Currency.CAD, Currency.USD}


@given(
    balance_cents=st.integers(min_value=3100, max_value=10**10),
    currency=currencies,
    day=st.dates(min_value=date(2026, 1, 1), max_value=date(2026, 12, 23)),
)
def test_interest_accruals_balance(balance_cents: int, currency: Currency, day: date) -> None:
    balance = Money(Decimal(balance_cents).scaleb(-2), currency)
    amount = daily_interest(balance, Decimal("0.06"))
    assume(amount.amount > 0)
    event = interest(
        amount=str(amount.amount),
        debit_balance=str(balance.amount),
        currency=currency,
        accrual_date=day,
    )
    assert_balanced(engine().post(event))


@given(
    side=sides,
    quantity=quantities,
    price=prices,
    commission=commissions,
    clearing_fee=commissions,
    currency=currencies,
)
def test_executions_with_clearing_fees_balance(
    side: Side,
    quantity: str,
    price: str,
    commission: str,
    clearing_fee: str,
    currency: Currency,
) -> None:
    gross = (Decimal(quantity) * Decimal(price)).quantize(Decimal("0.01"))
    assume(gross != 0 or Decimal(commission) != 0 or Decimal(clearing_fee) != 0)
    batch = engine().post(
        execution(
            side=side,
            quantity=quantity,
            price=price,
            commission=commission,
            clearing_fee=clearing_fee,
            currency=currency,
        )
    )
    assert_balanced(batch)
