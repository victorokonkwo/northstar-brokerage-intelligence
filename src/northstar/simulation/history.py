"""Synthetic market history before the dataset window (roadmap 5A; owner decisions 2026-11-20).

Historical-simulation VaR needs 250 one-day scenarios (OQ-08), and the dataset
window has about 60 trading days. This adds a year of synthetic equity closes
and USD/CAD reference rates before the window, as two external record sets from
a market-data vendor: ``price_history`` and ``fx_rate_history``. They are not
canonical events and nothing reconciles them; the risk models read them.

- **Calendar:** every weekday from ``history.start`` to ``history.end``. It is a
  synthetic history calendar: it makes no claim about which days real
  exchanges were open (owner decision: no verified 2025 calendars were
  available, and the history is synthetic anyway).
- **Continuity:** each path is walked backward from its anchor, the
  instrument's ``start_price`` (the FX ``start_rate``): the value the window's
  own path moves away from on its first day. So the history's last value is
  the anchor, and the window's prices are unchanged.
- **Determinism:** the draws come from their own stream, child
  ``HISTORY_STREAM`` of ``SeedSequence(seed)``. Adding or changing the history
  changes no other path in the dataset (W4.1).
- **Scope:** equities only. Options are repriced by the risk model from their
  underlying, so they need no price history.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal
from typing import Final, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict

from northstar.common.money import Currency
from northstar.domain.events import FxRate, Price
from northstar.domain.settlement import AssetClass
from northstar.simulation.market import PRICE_TICK, RATE_PLACES

HISTORY_STREAM: Final = 0x48495354
"""Spawn key of the history's random stream ("HIST"). The dataset's other streams
are children 0 to the account count of the same seed, far below this."""

_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PriceHistoryRecord(_Frozen):
    """The vendor's synthetic daily close for an equity, before the dataset window."""

    record_type: Literal["price_history_record"] = "price_history_record"
    record_id: str
    instrument_id: str
    price_date: date
    close_price: Price
    currency: Currency


class FxRateHistoryRecord(_Frozen):
    """The vendor's synthetic daily USD/CAD reference rate (CAD per USD), before the window."""

    record_type: Literal["fx_rate_history_record"] = "fx_rate_history_record"
    record_id: str
    base_currency: Currency
    quote_currency: Currency
    rate_date: date
    rate: FxRate


HISTORY_MODELS: Final[dict[str, type[BaseModel]]] = {
    "price_history": PriceHistoryRecord,
    "fx_rate_history": FxRateHistoryRecord,
}
"""The history record sets, by name (ingested like the other external record sets)."""

_PREFIX: Final = {"price_history": "pxh", "fx_rate_history": "fxh"}


@dataclass(frozen=True, slots=True)
class HistorySpec:
    """One equity's path parameters: its anchor price and daily volatility."""

    instrument_id: str
    currency: Currency
    asset_class: AssetClass
    anchor: Decimal
    daily_vol: Decimal


@dataclass(frozen=True, slots=True)
class MarketHistory:
    """The synthetic history: equity closes and USD/CAD rates, each in date order."""

    price_history: tuple[PriceHistoryRecord, ...]
    fx_rate_history: tuple[FxRateHistoryRecord, ...]

    def by_name(self) -> dict[str, tuple[BaseModel, ...]]:
        """Each record set by its name, as the dataset's ``external/`` files are named."""
        return {"price_history": self.price_history, "fx_rate_history": self.fx_rate_history}


def history_days(start: date, end: date) -> list[date]:
    """Every weekday from ``start`` to ``end``, inclusive (the synthetic history calendar)."""
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def backward_path(
    rng: np.random.Generator,
    anchor: Decimal,
    daily_vol: Decimal,
    days: Sequence[date],
    quantum: Decimal,
) -> dict[date, Decimal]:
    """A log-normal path over ``days`` that ends at ``anchor``, walked backward in time.

    The last day's value is ``anchor``. Each earlier day undoes one log-normal
    step of the same form as the window's paths (``simulation.market``):
    value_{t-1} = value_t / exp(vol * z - vol^2 / 2). Values are rounded
    half-even to ``quantum`` and never fall below one quantum; the unrounded
    level carries the path.

    Raises:
        ValueError: ``anchor`` or ``daily_vol`` is not positive.
    """
    if anchor <= 0 or daily_vol <= 0:
        msg = "anchor and daily_vol must be positive"
        raise ValueError(msg)
    if not days:
        return {}
    drift = -(daily_vol * daily_vol) / 2
    ordered = sorted(days)
    path = {ordered[-1]: anchor.quantize(quantum, rounding=ROUND_HALF_EVEN)}
    level = anchor
    draws = rng.standard_normal(len(ordered) - 1)
    for day, z in zip(reversed(ordered[:-1]), draws, strict=True):
        shock = _CONTEXT.add(_CONTEXT.multiply(daily_vol, Decimal(float(z))), drift)
        level = _CONTEXT.divide(level, _CONTEXT.exp(shock))
        path[day] = max(level.quantize(quantum, rounding=ROUND_HALF_EVEN), quantum)
    return dict(sorted(path.items()))


def generate_history(
    specs: Sequence[HistorySpec],
    fx_anchor: Decimal,
    fx_daily_vol: Decimal,
    start: date,
    end: date,
    seed: int,
) -> MarketHistory:
    """The synthetic history of the equities in ``specs`` and of USD/CAD, from ``start`` to ``end``.

    Equities are walked in the order given, then the FX rate, all from the
    history stream of ``seed``.
    """
    rng = np.random.Generator(
        np.random.PCG64(np.random.SeedSequence(seed, spawn_key=(HISTORY_STREAM,)))
    )
    days = history_days(start, end)
    prices: list[PriceHistoryRecord] = []
    for spec in (s for s in specs if s.asset_class is AssetClass.EQUITY):
        path = backward_path(rng, spec.anchor, spec.daily_vol, days, PRICE_TICK)
        offset = len(prices)  # fixed before extend, which grows the list as it goes
        prices.extend(
            PriceHistoryRecord(
                record_id=_record_id("price_history", seed, offset + n),
                instrument_id=spec.instrument_id,
                price_date=day,
                close_price=close,
                currency=spec.currency,
            )
            for n, (day, close) in enumerate(path.items(), start=1)
        )
    rates_path = backward_path(rng, fx_anchor, fx_daily_vol, days, RATE_PLACES)
    rates = tuple(
        FxRateHistoryRecord(
            record_id=_record_id("fx_rate_history", seed, n),
            base_currency=Currency.USD,
            quote_currency=Currency.CAD,
            rate_date=day,
            rate=rate,
        )
        for n, (day, rate) in enumerate(rates_path.items(), start=1)
    )
    return MarketHistory(price_history=tuple(prices), fx_rate_history=rates)


def _record_id(record_set: str, seed: int, sequence: int) -> str:
    """The vendor's own reference for a record, in the style of the other external sets."""
    return f"{_PREFIX[record_set]}_s{seed}_{sequence:06d}"
