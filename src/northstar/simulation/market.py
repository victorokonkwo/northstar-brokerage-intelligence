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

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal
from itertools import pairwise
from typing import Final

import numpy as np

from northstar.domain.events import ExerciseStyle, OptionRight
from northstar.domain.market_risk.options import (
    Dividend,
    OptionInputs,
    binomial,
    exercise_is_optimal,
)

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
    drops: Mapping[date, Decimal] | None = None,
) -> dict[date, Decimal]:
    """Return one value per day in ``days`` (in order), starting from ``start``.

    The first day already moves from ``start``. Values are rounded half-even to
    ``quantum`` and never fall below one quantum. On a day in ``drops`` (an
    ex-dividend date), the level first falls by the dividend, then moves:
    close = (previous level - dividend) x exp(shock).

    Raises:
        ValueError: ``start`` or ``daily_vol`` is not positive, or a dividend is
            worth the whole level.
    """
    if start <= 0 or daily_vol <= 0:
        msg = "start and daily_vol must be positive"
        raise ValueError(msg)
    drift = -(daily_vol * daily_vol) / 2
    path: dict[date, Decimal] = {}
    level = start
    for day, z in zip(days, rng.standard_normal(len(days)), strict=True):
        if drops and day in drops:
            level -= drops[day]
            if level <= 0:
                msg = f"the dividend going ex on {day} is worth the whole price"
                raise ValueError(msg)
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
    dividends: Sequence[tuple[date, Decimal]] = (),
) -> Decimal:
    """The option's close on ``day``: its tree price on the underlying's close that day.

    ``dividends`` are the underlying's known cash dividends, as (ex-date, amount
    per share); the model escrows those going ex after ``day`` (the pricer
    ignores any after expiry; roadmap 5C-a). The price is rounded half-even to
    a cent and never falls below one cent, like every synthetic close.

    Raises:
        ValueError: ``day`` is after expiry: the option is gone.
    """
    return model_close(
        model_inputs(
            right, style, strike, expiry, underlying_close, day, rate, implied_vol, dividends
        )
    )


def model_close(terms: OptionInputs) -> Decimal:
    """The tree price of ``terms``, rounded half-even to a cent, never below one cent."""
    value = Decimal(binomial(terms, OPTION_TREE_STEPS))
    return max(value.quantize(PRICE_TICK, rounding=ROUND_HALF_EVEN), PRICE_TICK)


def early_exercise_optimal(terms: OptionInputs, expiry: date, day: date) -> bool:
    """Whether a holder should exercise this American option after ``day``'s close.

    The simulator's clients decide once a day, so the tree has one step per
    calendar day to expiry: exercising now is optimal when waiting for the next
    day's chance is worth no more (roadmap 5C-b, ADR 013).
    """
    return exercise_is_optimal(terms, max((expiry - day).days, 1))


def model_inputs(
    right: OptionRight,
    style: ExerciseStyle,
    strike: Decimal,
    expiry: date,
    underlying_close: Decimal,
    day: date,
    rate: Decimal,
    implied_vol: Decimal,
    dividends: Sequence[tuple[date, Decimal]] = (),
) -> OptionInputs:
    """The model's inputs for an option on ``day`` (see ``model_option_close``).

    Raises:
        ValueError: ``day`` is after expiry: the option is gone.
    """
    if day > expiry:
        msg = f"the option expired on {expiry}; {day} has no model price"
        raise ValueError(msg)
    return OptionInputs(
        right=right,
        style=style,
        spot=float(underlying_close),
        strike=float(strike),
        rate=float(rate),
        vol=float(implied_vol),
        expiry=(expiry - day).days / DAYS_PER_YEAR,
        dividends=tuple(
            Dividend(time=(ex - day).days / DAYS_PER_YEAR, amount=float(amount))
            for ex, amount in sorted(dividends)
            if day < ex  # gone ex: already in the price; after expiry: the pricer ignores it
        ),
    )
