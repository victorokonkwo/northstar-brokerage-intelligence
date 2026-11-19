"""Trade amounts and commissions shared by the brokerage simulator and the ledger.

There is one implementation of each amount (ADR 007), so the settlement
obligation and the journal lines can never disagree on what a fill is worth.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

from northstar.common.exceptions import PolicyNotFoundError
from northstar.common.money import Currency, Money
from northstar.domain.events import FeeRate, NonNegativeAmount


def gross_amount(quantity: Decimal, price: Decimal, multiplier: int, currency: Currency) -> Money:
    """Return G = quantity * price * multiplier, rounded half-even to minor units.

    This is the amount posted for one execution (domain model §9.2). Rounding
    happens here, once per execution, because this is the posting point
    (ADR 004, rule 3).
    """
    unrounded = Money(price, currency) * quantity * Decimal(multiplier)
    return unrounded.quantize()


class CommissionBasis(StrEnum):
    """How a commission rate is applied to an execution."""

    PER_EXECUTION = "PER_EXECUTION"
    PER_CONTRACT = "PER_CONTRACT"


class CommissionRate(BaseModel):
    """Client commission for one market and currency (OQ-02: charged per execution)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    currency: Currency
    basis: CommissionBasis
    rate: NonNegativeAmount


class ClearingFeeRate(BaseModel):
    """Fee the clearing firm charges the broker per unit traded (a firm expense)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    currency: Currency
    per_unit: FeeRate


class FeeSchedule(BaseModel):
    """Versioned fee schedule (policy; values are illustrative).

    Raises:
        pydantic.ValidationError: On construction, if a market and currency
            pair has more than one commission or clearing-fee rate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    effective_from: date
    illustrative: bool
    commissions: tuple[CommissionRate, ...]
    clearing_fees: tuple[ClearingFeeRate, ...]

    @model_validator(mode="after")
    def _one_rate_per_market_and_currency(self) -> Self:
        for kind, keys in (
            ("commission", [(c.market, c.currency) for c in self.commissions]),
            ("clearing fee", [(c.market, c.currency) for c in self.clearing_fees]),
        ):
            if len(keys) != len(set(keys)):
                msg = f"a market and currency pair has more than one {kind} rate"
                raise ValueError(msg)
        return self

    def commission(
        self, market: str, currency: Currency, trade_date: date, quantity: Decimal
    ) -> Money:
        """Return the client commission for one execution of ``quantity`` units.

        Per-contract commissions are ``rate x quantity``, rounded half-even to
        minor units.

        Raises:
            PolicyNotFoundError: The schedule is not effective on ``trade_date``
                or has no commission for the market and currency.
        """
        self._require_effective(trade_date)
        for rate in self.commissions:
            if rate.market == market and rate.currency is currency:
                if rate.basis is CommissionBasis.PER_EXECUTION:
                    return Money(rate.rate, currency)
                return (Money(rate.rate, currency) * quantity).quantize()
        msg = f"fee schedule {self.version} has no commission for {market} {currency}"
        raise PolicyNotFoundError(msg)

    def clearing_fee(
        self, market: str, currency: Currency, trade_date: date, quantity: Decimal
    ) -> Money:
        """Return the clearing fee for one execution: ``per_unit x quantity``, rounded.

        Raises:
            PolicyNotFoundError: The schedule is not effective on ``trade_date``
                or has no clearing fee for the market and currency.
        """
        self._require_effective(trade_date)
        for rate in self.clearing_fees:
            if rate.market == market and rate.currency is currency:
                return (Money(rate.per_unit, currency) * quantity).quantize()
        msg = f"fee schedule {self.version} has no clearing fee for {market} {currency}"
        raise PolicyNotFoundError(msg)

    def _require_effective(self, trade_date: date) -> None:
        if trade_date < self.effective_from:
            msg = (
                f"fee schedule {self.version} is effective from {self.effective_from}, "
                f"not on trade date {trade_date}"
            )
            raise PolicyNotFoundError(msg)
