"""Synthetic market data: daily closes and a USD/CAD reference-rate path.

These values are SYNTHETIC (labeled ``source: synthetic`` on every event). They
come from a seeded log-normal walk:

    close_t = close_{t-1} * exp(sigma * z_t - sigma^2 / 2),  z_t ~ N(0, 1)

Only the standard normal draws come from numpy's ``Generator`` (PCG64, which
is deterministic across platforms). Everything after that, including ``exp``,
is ``Decimal`` arithmetic in an explicit context, so the paths are
byte-identical on every machine. Floating-point ``exp`` can differ across
CPUs and math libraries.
"""

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal
from itertools import pairwise

import numpy as np

_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)
PRICE_TICK = Decimal("0.01")
RATE_PLACES = Decimal("0.0001")
"""Synthetic reference rates are rounded to 4 decimal places (an illustrative choice)."""


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
