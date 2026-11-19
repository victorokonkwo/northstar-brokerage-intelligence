"""INV-01: every accepted journal batch balances per currency (domain model §8).

Property tests over generated events for every v1 posting rule. KPI-01 counts
the batches that would violate this; it must be zero.
"""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from northstar.common.exceptions import UnbalancedJournalError
from northstar.common.money import Currency, Money
from northstar.domain.accounting import EntrySide, JournalBatch
from northstar.domain.events import Side

from factories import (
    commissions,
    covered_timestamps,
    currencies,
    deposit,
    engine,
    execution,
    positive_amounts,
    prices,
    quantities,
    settlement,
    sides,
)


def assert_balanced(batch: JournalBatch) -> None:
    for debits, credits in batch.totals().values():
        assert debits == credits
    for line in batch.lines:
        assert line.amount.amount > 0
        assert line.amount.is_quantized


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
