"""Trade amounts and commissions shared by the brokerage simulator and the ledger.

There is one implementation of each amount (ADR 007), so the settlement
obligation and the journal lines can never disagree on what a fill is worth.
"""

from datetime import date
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from northstar.common.exceptions import PolicyNotFoundError
from northstar.common.money import Currency, Money
from northstar.domain.events import NonNegativeAmount


def gross_amount(quantity: Decimal, price: Decimal, multiplier: int, currency: Currency) -> Money:
    """Return G = quantity * price * multiplier, rounded half-even to minor units.

    This is the amount posted for one execution (domain model §9.2). Rounding
    happens here, once per execution, because this is the posting point
    (ADR 004, rule 3).
    """
    unrounded = Money(price, currency) * quantity * Decimal(multiplier)
    return unrounded.quantize()


class CommissionRate(BaseModel):
    """Client commission charged on each execution in one market and currency."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    currency: Currency
    per_execution: NonNegativeAmount


class FeeSchedule(BaseModel):
    """Versioned client fee schedule (policy; values are illustrative).

    Commission is charged per execution (OQ-02, owner decision 2026-11-19).

    Raises:
        pydantic.ValidationError: On construction, if a market and currency
            pair has more than one rate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    effective_from: date
    illustrative: bool
    commissions: tuple[CommissionRate, ...]

    @model_validator(mode="after")
    def _one_rate_per_market_and_currency(self) -> Self:
        keys = [(c.market, c.currency) for c in self.commissions]
        if len(keys) != len(set(keys)):
            msg = "a market and currency pair has more than one commission rate"
            raise ValueError(msg)
        return self

    def commission(self, market: str, currency: Currency, trade_date: date) -> Money:
        """Return the commission for one execution.

        Raises:
            PolicyNotFoundError: The schedule is not yet effective on
                ``trade_date``, or it has no rate for the market and currency.
        """
        if trade_date < self.effective_from:
            msg = (
                f"fee schedule {self.version} is effective from {self.effective_from}, "
                f"not on trade date {trade_date}"
            )
            raise PolicyNotFoundError(msg)
        for rate in self.commissions:
            if rate.market == market and rate.currency is currency:
                return Money(rate.per_execution, currency)
        msg = f"fee schedule {self.version} has no commission for {market} {currency}"
        raise PolicyNotFoundError(msg)
