"""Dividend entitlement and allocation (roadmap 5C-a, ADR 013)."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from northstar.domain.corporate_actions import dividend_allocations


def test_each_holder_gets_shares_times_rate_in_account_order() -> None:
    allocations = dividend_allocations(
        Decimal("0.25"), {"acc_2": Decimal(7), "acc_1": Decimal(100), "acc_3": Decimal(0)}
    )
    assert [(a.account_id, a.quantity, a.amount) for a in allocations] == [
        ("acc_1", Decimal(100), Decimal("25.00")),
        ("acc_2", Decimal(7), Decimal("1.75")),
    ]  # acc_3 holds nothing at the record date, so it gets nothing


def test_no_holders_means_no_allocations() -> None:
    assert dividend_allocations(Decimal("0.25"), {"acc_1": Decimal(0)}) == ()


def test_fractional_shares_are_refused() -> None:
    # Whole shares x a whole-cent rate is exact; fractions would need a rounding rule.
    with pytest.raises(ValueError, match="whole shares"):
        dividend_allocations(Decimal("0.25"), {"acc_1": Decimal("1.5")})


def test_a_rate_that_isnt_whole_cents_is_refused() -> None:
    with pytest.raises(ValueError, match="whole cents"):
        dividend_allocations(Decimal("0.255"), {"acc_1": Decimal(10)})


@given(
    holdings=st.dictionaries(
        st.from_regex(r"acc_[0-9]{3}", fullmatch=True), st.integers(0, 10**7), max_size=30
    ),
    cents=st.integers(1, 10**4),
)
def test_allocations_are_exact_and_cover_every_holder(holdings: dict[str, int], cents: int) -> None:
    rate = Decimal(cents).scaleb(-2)
    allocations = dividend_allocations(rate, {a: Decimal(q) for a, q in holdings.items()})
    assert {a.account_id for a in allocations} == {a for a, q in holdings.items() if q > 0}
    for a in allocations:
        assert a.amount == a.amount.quantize(Decimal("0.01"))  # exact, nothing rounded
        assert a.amount == holdings[a.account_id] * rate
