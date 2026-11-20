"""Synthetic market data: daily closes and a USD/CAD reference-rate path.

These values are SYNTHETIC (labeled ``source: synthetic`` on every event). They
come from a seeded log-normal walk:

    close_t = close_{t-1} * exp(sigma * z_t - sigma^2 / 2),  z_t ~ N(0, 1)

Only the standard normal draws come from numpy's ``Generator`` (PCG64, which
is deterministic across platforms). Everything after that, including ``exp``,
is ``Decimal`` arithmetic in an explicit context, so the paths are
byte-identical on every machine. Floating-point ``exp`` can differ across
CPUs and math libraries.

Option closes are not walks: each is the model price of the option on its
underlying's close that day (roadmap 5B; ADR 012), from the binomial tree of
``domain.market_risk.options``. That tree is bit-identical on every platform,
so these closes are too.
"""

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal
from itertools import pairwise
from typing import Final

import numpy as np

from northstar.domain.events import ExerciseStyle, OptionRight
from northstar.domain.market_risk.options import OptionInputs, binomial

_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)
PRICE_TICK = Decimal("0.01")
RATE_PLACES = Decimal("0.0001")
"""Synthetic reference rates are rounded to 4 decimal places (an illustrative choice)."""
OPTION_TREE_STEPS: Final = 200
"""Steps of the tree that prices synthetic option closes. The risk model reprices
with the same steps, so it recovers each option's configured implied vol up to
the rounding of the close to a cent (tests/unit/simulation/test_option_prices.py)."""
DAYS_PER_YEAR: Final = 365
"""Time to expiry is calendar days / 365 (an illustrative convention, ADR 012)."""


def log_normal_path(
    rng: np.random.Generator,
    start: Decimal,
    daily_vol: Decimal,
    days: Sequence[date],
    quantum: Decimal,
) -> dict[date, Decimal]:
    """Return one value per day in ``days`` (in order), starting from ``start``.

    The first day already moves from ``start``. Values are rounded half-even to
    ``quantum`` and never fall below one quantum.

    Raises:
        ValueError: ``start`` or ``daily_vol`` is not positive.
    """
    if start <= 0 or daily_vol <= 0:
        msg = "start and daily_vol must be positive"
        raise ValueError(msg)
    drift = -(daily_vol * daily_vol) / 2
    path: dict[date, Decimal] = {}
    level = start
    for day, z in zip(days, rng.standard_normal(len(days)), strict=True):
        shock = _CONTEXT.add(_CONTEXT.multiply(daily_vol, Decimal(float(z))), drift)
        level = _CONTEXT.multiply(level, _CONTEXT.exp(shock))
        path[day] = max(level.quantize(quantum, rounding=ROUND_HALF_EVEN), quantum)
    return path


def absolute_returns(path: dict[date, Decimal]) -> dict[date, Decimal]:
    """|close_t / close_{t-1} - 1| for each day after the first."""
    days = sorted(path)
    return {day: abs(_CONTEXT.divide(path[day], path[prev]) - 1) for prev, day in pairwise(days)}


def model_option_close(
    right: OptionRight,
    style: ExerciseStyle,
    strike: Decimal,
    expiry: date,
    underlying_close: Decimal,
    day: date,
    rate: Decimal,
    implied_vol: Decimal,
) -> Decimal:
    """The option's close on ``day``: its tree price on the underlying's close that day.

    No dividends are known to the model yet (they arrive in roadmap 5C). The
    price is rounded half-even to a cent and never falls below one cent, like
    every synthetic close.

    Raises:
        ValueError: ``day`` is after expiry (exercise and expiry are roadmap 5C).
    """
    if day > expiry:
        msg = f"the option expired on {expiry}; {day} has no model price"
        raise ValueError(msg)
    terms = OptionInputs(
        right=right,
        style=style,
        spot=float(underlying_close),
        strike=float(strike),
        rate=float(rate),
        vol=float(implied_vol),
        expiry=(expiry - day).days / DAYS_PER_YEAR,
    )
    value = Decimal(binomial(terms, OPTION_TREE_STEPS))
    return max(value.quantize(PRICE_TICK, rounding=ROUND_HALF_EVEN), PRICE_TICK)
