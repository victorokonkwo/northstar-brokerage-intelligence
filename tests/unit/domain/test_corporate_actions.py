"""Dividend entitlement and allocation (roadmap 5C-a, ADR 013)."""

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from northstar.common.exceptions import PolicyNotFoundError
from northstar.domain.corporate_actions import (
    OptionExpiryPolicy,
    dividend_allocations,
    intrinsic_value,
)
from northstar.domain.events import OptionRight
from northstar.infrastructure.config_loader import load_policy

from factories import CONFIG


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


class TestOptionExpiry:
    POLICY = load_policy(CONFIG / "brokerage" / "option_expiry_policy.yaml", OptionExpiryPolicy)

    @pytest.mark.parametrize(
        ("right", "close", "intrinsic"),
        [
            (OptionRight.CALL, "50.01", "0.01"),
            (OptionRight.CALL, "49.99", "0"),
            (OptionRight.PUT, "49.99", "0.01"),
            (OptionRight.PUT, "50.01", "0"),
        ],
    )
    def test_intrinsic_value_per_share(
        self, right: OptionRight, close: str, intrinsic: str
    ) -> None:
        assert intrinsic_value(right, Decimal("50.00"), Decimal(close)) == Decimal(intrinsic)

    @pytest.mark.parametrize(
        ("close", "exercised"),
        [("50.00", False), ("50.01", True), ("50.005", False), ("62.40", True), ("12.00", False)],
    )
    def test_exercise_by_exception_at_one_cent_in_the_money(
        self, close: str, exercised: bool
    ) -> None:
        decision = self.POLICY.exercises_at_expiry(
            OptionRight.CALL, Decimal("50.00"), Decimal(close), date(2026, 3, 20)
        )
        assert decision is exercised

    def test_an_at_the_money_option_expires_even_at_a_zero_threshold(self) -> None:
        zero = self.POLICY.model_copy(update={"auto_exercise_threshold": Decimal(0)})
        assert not zero.exercises_at_expiry(
            OptionRight.PUT, Decimal("50.00"), Decimal("50.00"), date(2026, 3, 20)
        )

    def test_the_policy_applies_only_from_its_effective_date(self) -> None:
        with pytest.raises(PolicyNotFoundError, match="effective from"):
            self.POLICY.exercises_at_expiry(
                OptionRight.CALL, Decimal("50.00"), Decimal("60.00"), date(2025, 12, 19)
            )
