"""Option pricing validation (NFR-11; FR-M3-05; roadmap 5B).

These tests check the pricer four ways:

- **Reference values:** a published example, recorded in docs/sources.md.
- **Hand computation:** a one-step tree.
- **No-arbitrage identities:** put-call parity, and an American call on a stock
  that pays no dividend.
- **Agreement between independent paths:** the tree against Black-Scholes, and
  the analytic Greeks against bumping and repricing.
"""

import math
from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from northstar.domain.events import ExerciseStyle, OptionRight
from northstar.domain.market_risk.options import (
    Dividend,
    OptionInputs,
    PricingError,
    aged,
    binomial,
    black_scholes,
    black_scholes_greeks,
    greeks,
    implied_vol,
    price,
    tree_greeks,
)

EUROPEAN, AMERICAN = ExerciseStyle.EUROPEAN, ExerciseStyle.AMERICAN
CALL, PUT = OptionRight.CALL, OptionRight.PUT


def option(
    right: OptionRight = CALL,
    style: ExerciseStyle = EUROPEAN,
    *,
    spot: float = 100.0,
    strike: float = 100.0,
    rate: float = 0.05,
    vol: float = 0.25,
    expiry: float = 0.5,
    dividends: tuple[Dividend, ...] = (),
) -> OptionInputs:
    return OptionInputs(right, style, spot, strike, rate, vol, expiry, dividends)


def drawn(
    right: OptionRight,
    style: ExerciseStyle,
    i: dict[str, float],
    dividends: tuple[Dividend, ...] = (),
) -> OptionInputs:
    """An option from a Hypothesis draw of ``inputs``."""
    return OptionInputs(
        right, style, i["spot"], i["strike"], i["rate"], i["vol"], i["expiry"], dividends
    )


inputs = st.fixed_dictionaries(
    {
        "spot": st.floats(20, 200),
        "strike": st.floats(20, 200),
        "rate": st.floats(0, 0.10),
        "vol": st.floats(0.05, 1.0),
        "expiry": st.floats(0.05, 2.0),
    }
)


class TestReferenceValues:
    def test_hull_example_15_6(self) -> None:
        """Hull, *Options, Futures and Other Derivatives*, 9th ed., Ex. 15.6.

        The values are as printed in the CRAN documentation of the R package
        QFRM, ``BS_Simple`` (docs/sources.md): S0 42, K 40, r 10%, vol 20%,
        T 0.5. The call is 4.759422 and the put 0.8085994.
        """
        call = black_scholes(option(CALL, spot=42, strike=40, rate=0.1, vol=0.2, expiry=0.5))
        put = black_scholes(option(PUT, spot=42, strike=40, rate=0.1, vol=0.2, expiry=0.5))
        assert call == pytest.approx(4.759422, abs=5e-7)
        assert put == pytest.approx(0.8085994, abs=5e-8)

    def test_a_one_step_tree_by_hand(self) -> None:
        up = math.exp(0.25 * math.sqrt(0.5))
        p = (math.exp(0.05 * 0.5) - 1 / up) / (up - 1 / up)
        by_hand = math.exp(-0.05 * 0.5) * p * (100 * up - 100)
        assert binomial(option(CALL), steps=1) == pytest.approx(by_hand, rel=1e-14)
        # The American put exercises at the root if that beats holding.
        put = option(PUT, AMERICAN, strike=130)
        assert binomial(put, steps=1) == pytest.approx(30.0, rel=1e-14)


class TestNoArbitrage:
    @given(inputs)
    def test_put_call_parity(self, i: dict[str, float]) -> None:
        call, put = black_scholes(drawn(CALL, EUROPEAN, i)), black_scholes(drawn(PUT, EUROPEAN, i))
        forward = i["spot"] - i["strike"] * math.exp(-i["rate"] * i["expiry"])
        assert call - put == pytest.approx(forward, abs=1e-9 * max(1.0, i["spot"]))

    @given(inputs)
    def test_parity_holds_with_dividends_on_the_escrowed_spot(self, i: dict[str, float]) -> None:
        dividends = (Dividend(i["expiry"] / 2, 1.0), Dividend(i["expiry"] * 2, 5.0))
        call = black_scholes(drawn(CALL, EUROPEAN, i, dividends))
        put = black_scholes(drawn(PUT, EUROPEAN, i, dividends))
        paid = math.exp(-i["rate"] * i["expiry"] / 2)  # only the first is before expiry
        forward = i["spot"] - paid - i["strike"] * math.exp(-i["rate"] * i["expiry"])
        assert call - put == pytest.approx(forward, abs=1e-9 * max(1.0, i["spot"]))

    @given(inputs)
    def test_an_american_call_without_dividends_is_never_exercised_early(
        self, i: dict[str, float]
    ) -> None:
        american = binomial(drawn(CALL, AMERICAN, i), steps=200)
        european = binomial(drawn(CALL, EUROPEAN, i), steps=200)
        assert american == pytest.approx(european, rel=1e-12, abs=1e-12)

    @given(inputs)
    def test_an_american_put_is_worth_at_least_european_and_intrinsic(
        self, i: dict[str, float]
    ) -> None:
        american = binomial(drawn(PUT, AMERICAN, i), steps=200)
        assert american >= binomial(drawn(PUT, EUROPEAN, i), steps=200) - 1e-12
        assert american >= max(i["strike"] - i["spot"], 0.0) - 1e-12

    def test_a_dividend_just_before_expiry_makes_early_exercise_of_a_call_worth_it(
        self,
    ) -> None:
        dividends = (Dividend(0.45, 8.0),)
        american = binomial(option(CALL, AMERICAN, strike=80, dividends=dividends))
        european = black_scholes(option(CALL, strike=80, dividends=dividends))
        assert american > european + 1.0


class TestTreeAgainstBlackScholes:
    @pytest.mark.parametrize("right", [CALL, PUT])
    @pytest.mark.parametrize("strike", [80.0, 100.0, 120.0])
    def test_the_tree_converges_to_black_scholes(self, right: OptionRight, strike: float) -> None:
        o = option(right, strike=strike)
        errors = [abs(binomial(o, steps=n) - black_scholes(o)) for n in (50, 2000)]
        assert errors[1] < 0.01
        assert errors[1] < errors[0]

    def test_with_dividends_it_converges_to_black_scholes_on_the_escrowed_spot(self) -> None:
        o = option(dividends=(Dividend(0.1, 1.5), Dividend(0.35, 1.5)))
        assert binomial(o, steps=2000) == pytest.approx(black_scholes(o), abs=0.01)

    def test_price_dispatches_on_exercise_style(self) -> None:
        assert price(option(PUT)) == black_scholes(option(PUT))
        assert price(option(PUT, AMERICAN), steps=300) == binomial(option(PUT, AMERICAN), 300)


class TestGreeks:
    @pytest.mark.parametrize("right", [CALL, PUT])
    @pytest.mark.parametrize("dividends", [(), (Dividend(0.2, 2.0), Dividend(0.4, 2.0))])
    def test_analytic_greeks_match_bumping_and_repricing(
        self, right: OptionRight, dividends: tuple[Dividend, ...]
    ) -> None:
        o = option(right, strike=105, dividends=dividends)
        g = black_scholes_greeks(o)
        h = 1e-4

        def bs(changed: OptionInputs) -> float:
            return black_scholes(changed)

        def bumped(field: str) -> float:
            value = getattr(o, field)
            up = bs(_replace(o, field, value + h))
            down = bs(_replace(o, field, value - h))
            return (up - down) / (2 * h)

        assert g.delta == pytest.approx(bumped("spot"), abs=1e-6)
        assert g.vega == pytest.approx(bumped("vol"), abs=1e-5)
        assert g.rho == pytest.approx(bumped("rate"), abs=1e-5)
        up, down = bs(_replace(o, "spot", o.spot + 1e-2)), bs(_replace(o, "spot", o.spot - 1e-2))
        assert g.gamma == pytest.approx((up - 2 * bs(o) + down) / 1e-4, abs=1e-5)
        assert g.theta == pytest.approx((bs(aged(o, 1e-5)) - bs(o)) / 1e-5, abs=1e-3)

    @pytest.mark.parametrize("right", [CALL, PUT])
    @pytest.mark.parametrize("strike", [90.0, 100.0, 120.0])
    def test_tree_greeks_match_the_analytic_ones_with_dividends(
        self, right: OptionRight, strike: float
    ) -> None:
        # European exercise, so the tree and Black-Scholes price the same thing.
        o = option(right, strike=strike, dividends=(Dividend(0.2, 2.0), Dividend(0.4, 2.0)))
        tree, analytic = tree_greeks(o, steps=1000), black_scholes_greeks(o)
        assert tree.delta == pytest.approx(analytic.delta, abs=1e-3)
        assert tree.gamma == pytest.approx(analytic.gamma, abs=1e-4)
        assert tree.theta == pytest.approx(analytic.theta, abs=1e-2)
        assert tree.rho == pytest.approx(analytic.rho, abs=2e-2)
        assert tree.vega == pytest.approx(analytic.vega, rel=1e-2)

    def test_american_greeks_match_european_where_exercise_never_pays(self) -> None:
        american, european = greeks(option(CALL, AMERICAN)), greeks(option(CALL))
        assert american.delta == pytest.approx(european.delta, abs=2e-3)
        assert american.gamma == pytest.approx(european.gamma, abs=2e-3)
        assert american.vega == pytest.approx(european.vega, abs=0.05)
        assert american.rho == pytest.approx(european.rho, abs=0.05)
        assert american.theta == pytest.approx(european.theta, abs=0.1)

    def test_a_deep_in_the_money_american_put_behaves_like_stock(self) -> None:
        g = greeks(option(PUT, AMERICAN, spot=50, strike=100))
        assert g.delta == pytest.approx(-1.0, abs=1e-9)
        assert g.gamma == pytest.approx(0.0, abs=1e-6)

    def test_aging_drops_the_dividends_paid_in_between(self) -> None:
        o = option(expiry=1.0, dividends=(Dividend(0.25, 1.0), Dividend(0.75, 1.0)))
        later = aged(o, 0.5)
        assert later.expiry == 0.5
        assert later.dividends == (Dividend(0.25, 1.0),)
        with pytest.raises(PricingError):
            aged(o, 1.5)


class TestImpliedVolatility:
    @pytest.mark.parametrize("style", [EUROPEAN, AMERICAN])
    @pytest.mark.parametrize("vol", [0.08, 0.3, 1.2])
    def test_it_recovers_the_volatility_that_priced_the_option(
        self, style: ExerciseStyle, vol: float
    ) -> None:
        o = option(PUT, style, vol=vol)  # at the money: worth more than exercising now
        assert price(o, steps=200) > o.intrinsic()
        assert implied_vol(o, price(o, steps=200), steps=200) == pytest.approx(vol, abs=1e-6)

    def test_a_price_every_low_vol_gives_leaves_the_vol_not_identified(self) -> None:
        # Deep in the money at low vol, the American put is worth exercising now,
        # so its price is its intrinsic value, 10, whatever the vol below a point.
        o = option(PUT, AMERICAN, strike=110, vol=0.05)
        assert price(o, steps=200) == pytest.approx(10.0, abs=1e-12)
        with pytest.raises(PricingError, match="not identified"):
            implied_vol(o, 10.0, steps=200)

    @pytest.mark.parametrize("market", [5.0, 101.0])
    def test_a_price_outside_the_no_arbitrage_bounds_is_refused(self, market: float) -> None:
        # A call on spot 100 struck at 90 is worth more than its intrinsic, 10,
        # and less than the spot.
        with pytest.raises(PricingError, match="no volatility"):
            implied_vol(option(CALL, strike=90), market)


class TestInputs:
    @pytest.mark.parametrize(
        "changes",
        [{"spot": 0.0}, {"strike": -1.0}, {"vol": 0.0}, {"expiry": -0.1}],
    )
    def test_invalid_inputs_are_refused(self, changes: dict[str, float]) -> None:
        with pytest.raises(PricingError):
            option(**changes)  # type: ignore[arg-type]

    def test_dividends_worth_the_whole_spot_are_refused(self) -> None:
        with pytest.raises(PricingError, match="whole spot"):
            black_scholes(option(dividends=(Dividend(0.1, 150.0),)))

    def test_at_expiry_the_price_is_the_payoff(self) -> None:
        assert price(option(PUT, AMERICAN, strike=110, expiry=0)) == 10.0
        assert price(option(CALL, strike=90, expiry=0)) == 10.0

    def test_black_scholes_refuses_american_exercise(self) -> None:
        with pytest.raises(PricingError, match="European"):
            black_scholes(option(style=AMERICAN))


def _replace(o: OptionInputs, field: str, value: float) -> OptionInputs:
    match field:
        case "spot":
            return replace(o, spot=value)
        case "vol":
            return replace(o, vol=value)
        case "rate":
            return replace(o, rate=value)
    raise AssertionError(field)
