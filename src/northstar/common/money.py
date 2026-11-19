"""Fixed-precision money, as decided in ADR 004.

Rules implemented here:

- An amount is a ``decimal.Decimal`` paired with an explicit ``Currency``.
  Floats are rejected, because they cannot represent most decimal fractions.
- Arithmetic runs in ``MONEY_CONTEXT``, an explicit decimal context, so results
  never depend on the calling thread's current context.
- Nothing is rounded during arithmetic. Rounding to the currency's minor unit
  happens only when ``Money.quantize`` is called, at posting or reporting time,
  with ``ROUND_HALF_EVEN``.
- ``allocate`` splits an amount with the largest-remainder method, so the parts
  always sum exactly to the original.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
)
from enum import StrEnum
from fractions import Fraction
from math import floor
from typing import Self

from northstar.common.exceptions import CurrencyMismatchError

MONEY_CONTEXT = Context(
    prec=28,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)
"""Decimal context for all money arithmetic (ADR 004, rule 2).

28 significant digits and ``ROUND_HALF_EVEN``. Invalid operations, division by
zero, and overflow raise instead of producing special values.
"""


class Currency(StrEnum):
    """Currencies the platform books in (domain model §6).

    Minor units come from the ISO 4217 list published by SIX Group
    (see docs/sources.md).
    """

    CAD = "CAD"
    USD = "USD"

    @property
    def minor_unit(self) -> int:
        """Number of decimal places in the currency's minor unit (cents = 2)."""
        return _MINOR_UNITS[self]


_MINOR_UNITS: dict[Currency, int] = {
    Currency.CAD: 2,
    Currency.USD: 2,
}


def _require_decimal(value: object, name: str) -> Decimal:
    """Return ``value`` if it is a finite ``Decimal``; raise otherwise.

    Raises:
        TypeError: ``value`` is not a ``Decimal`` (for example a ``float``).
        ValueError: ``value`` is NaN or infinite.
    """
    if not isinstance(value, Decimal):
        msg = f"{name} must be a decimal.Decimal, got {type(value).__name__}"
        raise TypeError(msg)
    if not value.is_finite():
        msg = f"{name} must be finite, got {value}"
        raise ValueError(msg)
    return value


def _require_currency(value: object) -> Currency:
    """Return ``value`` if it is a ``Currency``; raise ``TypeError`` otherwise.

    A plain string such as ``"CAD"`` is rejected, so every currency in the
    system comes from the ``Currency`` enum.
    """
    if not isinstance(value, Currency):
        msg = f"currency must be a Currency, got {type(value).__name__}"
        raise TypeError(msg)
    return value


@dataclass(frozen=True, slots=True)
class Money:
    """An immutable amount in a single currency.

    ``amount`` may carry more decimal places than the currency's minor unit
    while a calculation is in progress. Call ``quantize`` before posting or
    reporting the amount. The sign is the caller's convention: this type does
    not decide what positive and negative mean.

    Raises:
        TypeError: ``amount`` is not a ``Decimal`` or ``currency`` is not a
            ``Currency``.
        ValueError: ``amount`` is NaN or infinite.
    """

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        """Validate the field types (see the class docstring)."""
        _require_decimal(self.amount, "amount")
        _require_currency(self.currency)

    @classmethod
    def zero(cls, currency: Currency) -> Self:
        """Return a zero amount in ``currency``."""
        return cls(Decimal(0), currency)

    def __add__(self, other: object) -> "Money":
        """Return the exact sum of two amounts in the same currency.

        Raises:
            CurrencyMismatchError: The currencies differ.
        """
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other)
        return Money(MONEY_CONTEXT.add(self.amount, other.amount), self.currency)

    def __sub__(self, other: object) -> "Money":
        """Return the exact difference of two amounts in the same currency.

        Raises:
            CurrencyMismatchError: The currencies differ.
        """
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other)
        return Money(MONEY_CONTEXT.subtract(self.amount, other.amount), self.currency)

    def __neg__(self) -> "Money":
        """Return the amount with its sign reversed."""
        return Money(MONEY_CONTEXT.minus(self.amount), self.currency)

    def __mul__(self, factor: object) -> "Money":
        """Return the amount multiplied by a ``Decimal`` factor, unrounded.

        Use this for quantity-times-price calculations. Multiplying two
        ``Money`` values, or by a ``float``, is not supported.

        Raises:
            ValueError: ``factor`` is NaN or infinite.
        """
        if not isinstance(factor, Decimal):
            return NotImplemented
        _require_decimal(factor, "factor")
        return Money(MONEY_CONTEXT.multiply(self.amount, factor), self.currency)

    __rmul__ = __mul__

    def quantize(self) -> "Money":
        """Return the amount rounded to the currency's minor unit.

        Uses ``ROUND_HALF_EVEN`` (ADR 004, rule 3). Call it only where ADR 004
        allows rounding: when posting to the ledger or writing a report.
        """
        exponent = Decimal(1).scaleb(-self.currency.minor_unit)
        return Money(
            self.amount.quantize(exponent, rounding=ROUND_HALF_EVEN, context=MONEY_CONTEXT),
            self.currency,
        )

    @property
    def is_quantized(self) -> bool:
        """Whether the amount has no digits beyond the currency's minor unit."""
        return self.quantize().amount == self.amount

    def _require_same_currency(self, other: "Money") -> None:
        if other.currency is not self.currency:
            msg = f"cannot combine {self.currency} with {other.currency}"
            raise CurrencyMismatchError(msg)


def allocate(total: Money, weights: Sequence[Decimal]) -> tuple[Money, ...]:
    """Split ``total`` in proportion to ``weights`` using largest remainder.

    Every part is a whole number of minor units, and the parts sum exactly to
    ``total`` (ADR 004, rule 4). Each part differs from its exact proportional
    share by less than one minor unit. Leftover minor units go to the parts
    with the largest fractional remainders; ties go to the earliest part, so
    the result is deterministic. A negative total is split as its absolute
    value and every part negated.

    Args:
        total: The amount to split. Must already be quantized to its
            currency's minor unit.
        weights: One non-negative ``Decimal`` weight per part. At least one
            weight must be positive.

    Returns:
        One ``Money`` per weight, in the same order and currency as ``total``.

    Raises:
        ValueError: ``total`` is not quantized, ``weights`` is empty, a weight
            is negative, NaN, or infinite, or all weights are zero.
        TypeError: A weight is not a ``Decimal``.
    """
    if not total.is_quantized:
        msg = f"total must be quantized to {total.currency} minor units, got {total.amount}"
        raise ValueError(msg)
    if not weights:
        msg = "weights must not be empty"
        raise ValueError(msg)
    exact_weights = [Fraction(_require_decimal(w, "weight")) for w in weights]
    if any(w < 0 for w in exact_weights):
        msg = "weights must not be negative"
        raise ValueError(msg)
    weight_sum = sum(exact_weights, Fraction(0))
    if weight_sum == 0:
        msg = "at least one weight must be positive"
        raise ValueError(msg)

    minor_unit = total.currency.minor_unit
    total_units = int(total.amount.scaleb(minor_unit, context=MONEY_CONTEXT))
    sign = -1 if total_units < 0 else 1
    units_to_split = abs(total_units)

    exact_shares = [units_to_split * w / weight_sum for w in exact_weights]
    parts = [floor(share) for share in exact_shares]
    leftover = units_to_split - sum(parts)
    by_largest_remainder = sorted(
        range(len(parts)),
        key=lambda i: (-(exact_shares[i] - parts[i]), i),
    )
    for i in by_largest_remainder[:leftover]:
        parts[i] += 1

    return tuple(
        Money(Decimal(sign * units).scaleb(-minor_unit, context=MONEY_CONTEXT), total.currency)
        for units in parts
    )
