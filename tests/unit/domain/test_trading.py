"""Tests for ``northstar.domain.trading``: gross amounts and the fee schedule."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from northstar.common.exceptions import PolicyNotFoundError
from northstar.common.money import Currency, Money
from northstar.domain.trading import FeeSchedule, gross_amount

from factories import policies


def test_gross_amount_multiplies_then_rounds_once() -> None:
    assert gross_amount(Decimal(60), Decimal("52.37"), 1, Currency.CAD) == Money(
        Decimal("3142.20"), Currency.CAD
    )
    # 3 x 33.333333 = 99.999999 -> 100.00, rounded once at the end.
    assert gross_amount(Decimal(3), Decimal("33.333333"), 1, Currency.CAD).amount == Decimal(
        "100.00"
    )
    # Option-style multiplier: 2 contracts x 1.25 x 100.
    assert gross_amount(Decimal(2), Decimal("1.25"), 100, Currency.USD).amount == Decimal("250.00")


Q = Decimal(60)


def test_committed_fee_schedule_is_illustrative() -> None:
    schedule = policies().fee_schedule
    assert schedule.illustrative
    assert schedule.version == "2026.2"


@pytest.mark.parametrize(
    ("market", "currency", "quantity", "expected"),
    [
        ("XTSE", Currency.CAD, Decimal(60), "4.95"),  # per execution, any size
        ("XTSE", Currency.CAD, Decimal(1), "4.95"),
        ("XNYS", Currency.USD, Decimal(500), "4.95"),
        ("XCBO", Currency.USD, Decimal(3), "2.25"),  # 3 contracts x 0.75
        ("XCBO", Currency.USD, Decimal(1), "0.75"),
    ],
)
def test_commission(market: str, currency: Currency, quantity: Decimal, expected: str) -> None:
    fee = policies().fee_schedule.commission(market, currency, date(2026, 3, 3), quantity)
    assert fee == Money(Decimal(expected), currency)


@pytest.mark.parametrize(
    ("market", "currency", "quantity", "expected"),
    [
        ("XTSE", Currency.CAD, Decimal(60), "0.60"),
        ("XNYS", Currency.USD, Decimal(1000), "10.00"),
        ("XNYS", Currency.USD, Decimal("12.5"), "0.12"),  # 0.125 rounds half-even to 0.12
        ("XCBO", Currency.USD, Decimal(3), "0.00"),  # no options clearing fee set
    ],
)
def test_clearing_fee(market: str, currency: Currency, quantity: Decimal, expected: str) -> None:
    fee = policies().fee_schedule.clearing_fee(market, currency, date(2026, 3, 3), quantity)
    assert fee == Money(Decimal(expected), currency)


def test_no_fees_before_the_schedule_is_effective() -> None:
    schedule = policies().fee_schedule
    with pytest.raises(PolicyNotFoundError, match="effective from 2026-01-01"):
        schedule.commission("XTSE", Currency.CAD, date(2025, 12, 31), Q)
    with pytest.raises(PolicyNotFoundError, match="effective from 2026-01-01"):
        schedule.clearing_fee("XTSE", Currency.CAD, date(2025, 12, 31), Q)


def test_no_fees_for_an_unpriced_market_or_currency() -> None:
    schedule = policies().fee_schedule
    with pytest.raises(PolicyNotFoundError, match="XTSE USD"):
        schedule.commission("XTSE", Currency.USD, date(2026, 3, 3), Q)
    with pytest.raises(PolicyNotFoundError, match="XLON CAD"):
        schedule.clearing_fee("XLON", Currency.CAD, date(2026, 3, 3), Q)


def test_duplicate_rates_are_rejected() -> None:
    rate = {"market": "XTSE", "currency": "CAD", "basis": "PER_EXECUTION", "rate": "1.00"}
    fee = {"market": "XTSE", "currency": "CAD", "per_unit": "0.01"}
    base = {"version": "x", "effective_from": date(2026, 1, 1), "illustrative": True}
    with pytest.raises(ValidationError, match="more than one commission rate"):
        FeeSchedule.model_validate(base | {"commissions": [rate, rate], "clearing_fees": [fee]})
    with pytest.raises(ValidationError, match="more than one clearing fee rate"):
        FeeSchedule.model_validate(base | {"commissions": [rate], "clearing_fees": [fee, fee]})
