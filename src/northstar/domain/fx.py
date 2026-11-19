"""CAD/USD client conversions and FX spread revenue (OQ-01).

Owner decision (2026-11-19): the spread is revenue **at conversion**, measured
against the reference rate. Rates are CAD per 1 USD. The client rate is the
reference rate moved against the client by the policy spread:

- client buys USD: ``client_rate = reference x (1 + spread)``
- client sells USD: ``client_rate = reference x (1 - spread)``

All amounts are rounded half-even to minor units, once, where they are fixed.
This module is the single implementation of the arithmetic: the simulator
uses it to price a conversion, and the posting rule uses it to split the CAD
side into the reference value and the spread revenue (ADR 007).
"""

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from northstar.common.exceptions import NorthstarError, PolicyNotFoundError
from northstar.common.money import MONEY_CONTEXT, Currency, Money
from northstar.domain.events import FxRate

RATE_PLACES = 10
"""Rates carry 10 decimal places (numeric(18, 10), ADR 004)."""


class FxQuoteError(NorthstarError, ValueError):
    """Raised when a conversion would buy nothing or earn negative spread revenue."""


class FxPolicy(BaseModel):
    """Versioned FX conversion policy (illustrative values)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    effective_from: date
    illustrative: bool
    spread: Decimal = Field(gt=0, lt=1)
    rate_source: Literal["synthetic"]
    fallback: Literal["most_recent_prior"]


@dataclass(frozen=True, slots=True)
class FxQuote:
    """A priced conversion. ``spread_revenue`` is in CAD and never negative."""

    sell: Money
    buy: Money
    reference_rate: Decimal
    client_rate: Decimal
    reference_value_cad: Money
    spread_revenue: Money


def client_rate(reference_rate: Decimal, spread: Decimal, buy_currency: Currency) -> Decimal:
    """Return the client rate (CAD per USD) for a conversion into ``buy_currency``."""
    factor = Decimal(1) + spread if buy_currency is Currency.USD else Decimal(1) - spread
    return MONEY_CONTEXT.multiply(reference_rate, factor).quantize(
        Decimal(1).scaleb(-RATE_PLACES), context=MONEY_CONTEXT
    )


def reference_value_cad(sell: Money, buy: Money, reference_rate: Decimal) -> Money:
    """Return the CAD value of the USD leg at the reference rate, rounded."""
    usd = buy if buy.currency is Currency.USD else sell
    return Money(usd.amount * reference_rate, Currency.CAD).quantize()


def spread_revenue(sell: Money, buy: Money, reference_rate: Decimal) -> Money:
    """Return the CAD spread revenue of a conversion: the client's CAD versus reference value.

    Raises:
        FxQuoteError: The revenue would be negative (the client got a better
            rate than the reference rate after rounding).
    """
    ref_value = reference_value_cad(sell, buy, reference_rate)
    revenue = sell - ref_value if sell.currency is Currency.CAD else ref_value - buy
    if revenue.amount < 0:
        msg = f"conversion would earn negative spread revenue ({revenue.amount} CAD)"
        raise FxQuoteError(msg)
    return revenue


def quote(sell: Money, buy_currency: Currency, reference_rate: FxRate, spread: Decimal) -> FxQuote:
    """Price a client conversion of ``sell`` into ``buy_currency``.

    Raises:
        ValueError: The currencies are not CAD and USD.
        FxQuoteError: The amount is too small to buy anything, or so small
            that rounding would beat the reference rate.
    """
    if {sell.currency, buy_currency} != {Currency.CAD, Currency.USD}:
        msg = "conversions are between CAD and USD"
        raise ValueError(msg)
    rate = client_rate(reference_rate, spread, buy_currency)
    if buy_currency is Currency.USD:
        buy = Money(MONEY_CONTEXT.divide(sell.amount, rate), Currency.USD).quantize()
    else:
        buy = Money(sell.amount * rate, Currency.CAD).quantize()
    if buy.amount <= 0:
        msg = f"converting {sell.amount} {sell.currency} buys nothing"
        raise FxQuoteError(msg)
    # Rounding must never give the client a better rate than the reference
    # rate. That happens only for amounts of a few cents, and it would hide a
    # loss behind a rounded revenue of zero.
    if buy_currency is Currency.USD:
        beats_reference = buy.amount * reference_rate > sell.amount
    else:
        beats_reference = buy.amount > sell.amount * reference_rate
    if beats_reference:
        msg = (
            f"converting {sell.amount} {sell.currency} is too small: after rounding "
            "the client would get a better rate than the reference rate"
        )
        raise FxQuoteError(msg)
    return FxQuote(
        sell=sell,
        buy=buy,
        reference_rate=reference_rate,
        client_rate=rate,
        reference_value_cad=reference_value_cad(sell, buy, reference_rate),
        spread_revenue=spread_revenue(sell, buy, reference_rate),
    )


def reference_rate_on(day: date, rates: Mapping[date, Decimal]) -> tuple[Decimal, date]:
    """Return the reference rate for ``day`` and the date it was published.

    Fallback policy v1 (domain model §6): when ``day`` has no rate (a weekend or
    a holiday), the most recent prior rate applies, and its date is returned
    so the event records which rate was used.

    Raises:
        PolicyNotFoundError: No rate exists on or before ``day``.
    """
    dates = sorted(rates)
    index = bisect_right(dates, day)
    if index == 0:
        msg = f"no reference rate on or before {day}"
        raise PolicyNotFoundError(msg)
    rate_date = dates[index - 1]
    return rates[rate_date], rate_date
