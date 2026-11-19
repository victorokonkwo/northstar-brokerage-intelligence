"""Property tests for ``northstar.common.money`` over generated inputs."""

from decimal import Decimal
from fractions import Fraction

from hypothesis import given
from hypothesis import strategies as st

from northstar.common.money import Currency, Money, allocate

currencies = st.sampled_from(Currency)

# Posted amounts fit numeric(18, 2): at most 16 integer digits (ADR 004).
cents = st.integers(min_value=-(10**18) + 1, max_value=10**18 - 1)
posted_amounts = cents.map(lambda units: Decimal(units).scaleb(-2))

# Unrounded intermediate amounts with up to 8 decimal places.
raw_amounts = st.decimals(
    min_value=Decimal("-1e12"),
    max_value=Decimal("1e12"),
    places=8,
    allow_nan=False,
    allow_infinity=False,
)

weight_lists = st.lists(
    st.decimals(min_value=0, max_value=Decimal(10**6), places=4),
    min_size=1,
    max_size=20,
).filter(lambda ws: any(w > 0 for w in ws))


@given(total=posted_amounts, weights=weight_lists, currency=currencies)
def test_allocation_sums_exactly_to_the_total(
    total: Decimal, weights: list[Decimal], currency: Currency
) -> None:
    parts = allocate(Money(total, currency), weights)
    assert len(parts) == len(weights)
    assert sum((p.amount for p in parts), Decimal(0)) == total
    assert all(p.currency is currency and p.is_quantized for p in parts)


@given(total=posted_amounts, weights=weight_lists, currency=currencies)
def test_each_part_is_within_one_minor_unit_of_its_exact_share(
    total: Decimal, weights: list[Decimal], currency: Currency
) -> None:
    parts = allocate(Money(total, currency), weights)
    weight_sum = sum(weights, Decimal(0))
    one_unit = Fraction(1, 10**currency.minor_unit)
    for part, weight in zip(parts, weights, strict=True):
        exact_share = Fraction(total) * Fraction(weight) / Fraction(weight_sum)
        assert abs(Fraction(part.amount) - exact_share) < one_unit


@given(a=raw_amounts, b=raw_amounts, c=raw_amounts, currency=currencies)
def test_addition_is_exact_commutative_and_associative(
    a: Decimal, b: Decimal, c: Decimal, currency: Currency
) -> None:
    x, y, z = Money(a, currency), Money(b, currency), Money(c, currency)
    assert x + y == y + x
    assert (x + y) + z == x + (y + z)
    assert (x + y).amount == a + b
    assert (x + y) - y == x


@given(amount=raw_amounts, currency=currencies)
def test_quantize_is_idempotent_and_within_half_a_minor_unit(
    amount: Decimal, currency: Currency
) -> None:
    quantized = Money(amount, currency).quantize()
    assert quantized.is_quantized
    assert quantized.quantize() == quantized
    half_unit = Decimal(1).scaleb(-currency.minor_unit) / 2
    assert abs(quantized.amount - amount) <= half_unit
