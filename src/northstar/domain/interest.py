"""Daily margin interest on client debit balances (owner decision 2026-11-19).

Interest accrues every calendar day on the end-of-day debit (loan) balance of
each currency, at the policy's annual rate on an ACT/365 basis. Each day's
amount is rounded half-even to minor units when it is posted. The balance is
the trade-date 2100 balance (†, a documented simplification: real brokers
usually charge on settled balances).
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from northstar.common.money import MONEY_CONTEXT, Currency, Money
from northstar.domain.events import PositiveAmount

DAYS_IN_YEAR = 365
"""ACT/365: every year has 365 days for interest."""


class MaxDebitBalance(BaseModel):
    """Simulator guard on loan size. **Not** a margin requirement (Phase 5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    amount: PositiveAmount
    currency: Currency


class MarginPolicy(BaseModel):
    """Versioned margin-lending policy (illustrative values)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    effective_from: date
    illustrative: bool
    annual_rate: Decimal = Field(gt=0, lt=1)
    day_count: Literal["ACT/365"]
    max_debit_balance: MaxDebitBalance


def daily_interest(debit_balance: Money, annual_rate: Decimal) -> Money:
    """Return one day of interest on a positive debit balance, rounded half-even.

    Raises:
        ValueError: ``debit_balance`` is not positive.
    """
    if debit_balance.amount <= 0:
        msg = f"interest accrues only on a positive debit balance, got {debit_balance.amount}"
        raise ValueError(msg)
    daily = MONEY_CONTEXT.divide(
        MONEY_CONTEXT.multiply(debit_balance.amount, annual_rate), Decimal(DAYS_IN_YEAR)
    )
    return Money(daily, debit_balance.currency).quantize()
