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


def test_committed_fee_schedule_is_illustrative_and_per_execution() -> None:
    schedule = policies().fee_schedule
    assert schedule.illustrative
    assert schedule.commission("XTSE", Currency.CAD, date(2026, 3, 3)) == Money(
        Decimal("4.95"), Currency.CAD
    )


def test_no_commission_before_the_schedule_is_effective() -> None:
    with pytest.raises(PolicyNotFoundError, match="effective from 2026-01-01"):
        policies().fee_schedule.commission("XTSE", Currency.CAD, date(2025, 12, 31))


def test_no_commission_for_an_unpriced_market_or_currency() -> None:
    with pytest.raises(PolicyNotFoundError, match="XNYS USD"):
        policies().fee_schedule.commission("XNYS", Currency.USD, date(2026, 3, 3))
    with pytest.raises(PolicyNotFoundError, match="XTSE USD"):
        policies().fee_schedule.commission("XTSE", Currency.USD, date(2026, 3, 3))


def test_duplicate_rates_are_rejected() -> None:
    rate = {"market": "XTSE", "currency": "CAD", "per_execution": "1.00"}
    with pytest.raises(ValidationError, match="more than one commission rate"):
        FeeSchedule.model_validate(
            {
                "version": "x",
                "effective_from": date(2026, 1, 1),
                "illustrative": True,
                "commissions": [rate, rate],
            }
        )
