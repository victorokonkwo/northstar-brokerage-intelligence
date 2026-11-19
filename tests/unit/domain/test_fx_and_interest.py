"""Hand-computed FX conversions (OQ-01) and daily margin interest."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from northstar.common.money import Currency, Money
from northstar.domain.fx import FxPolicy, FxQuoteError, client_rate, quote
from northstar.domain.interest import MarginPolicy, daily_interest
from northstar.infrastructure.config_loader import load_policy

from factories import CONFIG

REF = Decimal("1.3700000000")
SPREAD = Decimal("0.015")


def cad(amount: str) -> Money:
    return Money(Decimal(amount), Currency.CAD)


def usd(amount: str) -> Money:
    return Money(Decimal(amount), Currency.USD)


class TestQuote:
    def test_client_buys_usd(self) -> None:
        # client rate 1.37 x 1.015 = 1.39055; 1000 / 1.39055 = 719.1399... -> 719.14
        # reference value 719.14 x 1.37 = 985.2218 -> 985.22; spread 1000.00 - 985.22
        q = quote(cad("1000.00"), Currency.USD, REF, SPREAD)
        assert q.client_rate == Decimal("1.3905500000")
        assert q.buy == usd("719.14")
        assert q.reference_value_cad == cad("985.22")
        assert q.spread_revenue == cad("14.78")

    def test_client_sells_usd(self) -> None:
        # client rate 1.37 x 0.985 = 1.34945; 1000 x 1.34945 = 1349.45
        # reference value 1000 x 1.37 = 1370.00; spread 1370.00 - 1349.45
        q = quote(usd("1000.00"), Currency.CAD, REF, SPREAD)
        assert q.client_rate == Decimal("1.3494500000")
        assert q.buy == cad("1349.45")
        assert q.reference_value_cad == cad("1370.00")
        assert q.spread_revenue == cad("20.55")

    def test_spread_revenue_is_always_in_cad(self) -> None:
        for sell, buy_currency in ((cad("50.00"), Currency.USD), (usd("50.00"), Currency.CAD)):
            assert quote(sell, buy_currency, REF, SPREAD).spread_revenue.currency is Currency.CAD

    def test_same_currency_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="between CAD and USD"):
            quote(cad("10.00"), Currency.CAD, REF, SPREAD)

    def test_rounding_may_not_beat_the_reference_rate(self) -> None:
        # CAD 0.01 / 1.39055 = USD 0.0072, which rounds half-even to USD 0.01. That is
        # an effective rate of 1.00 against a reference of 1.37.
        with pytest.raises(FxQuoteError, match="better rate than the reference"):
            quote(cad("0.01"), Currency.USD, REF, SPREAD)

    def test_a_conversion_that_buys_nothing_is_rejected(self) -> None:
        # At a rate of 5.075 CAD per USD, CAD 0.01 buys USD 0.0020 -> 0.00.
        with pytest.raises(FxQuoteError, match="buys nothing"):
            quote(cad("0.01"), Currency.USD, Decimal("5.0000000000"), SPREAD)

    @given(
        cents=st.integers(min_value=1000, max_value=10**10),  # 10.00 and up
        to_usd=st.booleans(),
        ref=st.decimals(min_value=Decimal("1.2"), max_value=Decimal("1.5"), places=6),
    )
    def test_revenue_is_non_negative_and_bounded_by_the_spread(
        self, cents: int, to_usd: bool, ref: Decimal
    ) -> None:
        sell = Money(Decimal(cents).scaleb(-2), Currency.CAD if to_usd else Currency.USD)
        q = quote(sell, Currency.USD if to_usd else Currency.CAD, ref, SPREAD)
        assert q.spread_revenue.amount >= 0
        # The revenue is about spread x CAD notional; allow two cents of rounding.
        notional_cad = q.reference_value_cad.amount
        assert q.spread_revenue.amount <= notional_cad * SPREAD * Decimal("1.02") + Decimal("0.02")

    def test_client_rate_moves_against_the_client(self) -> None:
        assert client_rate(REF, SPREAD, Currency.USD) > REF
        assert client_rate(REF, SPREAD, Currency.CAD) < REF


class TestInterest:
    @pytest.mark.parametrize(
        ("balance", "expected"),
        [
            ("10000.00", "1.64"),  # 10000 x 0.06 / 365 = 1.6438...
            ("50000.00", "8.22"),  # 8.2191...
            ("100.00", "0.02"),  # 0.01643...
            ("30.41", "0.00"),  # 0.004999... rounds to zero
        ],
    )
    def test_daily_interest_act_365(self, balance: str, expected: str) -> None:
        assert daily_interest(cad(balance), Decimal("0.06")) == cad(expected)

    @pytest.mark.parametrize("balance", ["0.00", "-5.00"])
    def test_only_positive_debit_balances_accrue(self, balance: str) -> None:
        with pytest.raises(ValueError, match="positive debit balance"):
            daily_interest(cad(balance), Decimal("0.06"))


def test_committed_policies_are_the_owner_approved_illustrative_values() -> None:
    fx = load_policy(CONFIG / "brokerage" / "fx_policy.yaml", FxPolicy)
    margin = load_policy(CONFIG / "brokerage" / "margin_policy.yaml", MarginPolicy)
    assert (fx.illustrative, fx.spread, fx.rate_source) == (True, Decimal("0.0150"), "synthetic")
    assert margin.illustrative
    assert margin.annual_rate == Decimal("0.0600")
    assert margin.day_count == "ACT/365"
    assert (margin.max_debit_balance.amount, margin.max_debit_balance.currency) == (
        Decimal("50000.00"),
        Currency.CAD,
    )
