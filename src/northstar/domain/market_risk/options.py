"""Option pricing and Greeks (FR-M3-05; ADR 012).

- **European exercise:** Black-Scholes, with analytic Greeks.
- **American exercise:** a Cox-Ross-Rubinstein binomial tree, with Greeks by
  bumping and repricing.
- **Known cash dividends** (both models) are escrowed: the price process is
  the spot less the present value of the dividends paid before expiry. On the
  tree, an exercise decision sees that process plus the present value of the
  dividends still to come. This is a standard approximation; its limits are in
  ADR 012.
- **Implied volatility** is found by bisection, and is refused when the price
  breaks a no-arbitrage bound.

These are model calculations in float64, which ADR 004 allows for risk
analytics. Callers convert the results to Decimal at the boundary and record
them as model outputs. Rates are annual and continuously compounded, and times
are in years.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from northstar.common.exceptions import NorthstarError


class Right(StrEnum):
    """Call or put."""

    CALL = "CALL"
    PUT = "PUT"


class ExerciseStyle(StrEnum):
    """When the holder may exercise."""

    EUROPEAN = "EUROPEAN"
    AMERICAN = "AMERICAN"


class PricingError(NorthstarError):
    """An option can't be priced from these inputs (a calculation failure, FR-M3-06)."""


@dataclass(frozen=True, slots=True)
class Dividend:
    """A known cash dividend per share, paid ``time`` years from now."""

    time: float
    amount: float


@dataclass(frozen=True, slots=True)
class OptionInputs:
    """Everything a price depends on.

    ``spot`` is the underlying's price; ``strike`` the exercise price;
    ``rate`` the risk-free rate; ``vol`` the annual volatility; ``expiry`` the
    time to expiry in years; ``dividends`` the known cash dividends before it.
    """

    right: Right
    style: ExerciseStyle
    spot: float
    strike: float
    rate: float
    vol: float
    expiry: float
    dividends: tuple[Dividend, ...] = ()

    def __post_init__(self) -> None:
        """Reject inputs no option can be priced from."""
        if not (self.spot > 0 and self.strike > 0 and self.vol > 0 and self.expiry >= 0):
            msg = (
                f"spot {self.spot}, strike {self.strike}, vol {self.vol}, and expiry "
                f"{self.expiry} must be positive (expiry may be 0)"
            )
            raise PricingError(msg)
        if any(d.amount < 0 or d.time < 0 for d in self.dividends):
            msg = "dividends must be non-negative and in the future"
            raise PricingError(msg)

    def intrinsic(self) -> float:
        """The value of exercising now."""
        return payoff(self.right, self.spot, self.strike)

    def dividend_pv(self) -> float:
        """The present value of the dividends paid before expiry."""
        return _pv_dividends_after(self.dividends, 0.0, self.expiry, self.rate)

    def escrowed_spot(self) -> float:
        """The spot less the present value of the dividends paid before expiry."""
        pv = self.dividend_pv()
        if pv >= self.spot:
            msg = "the dividends before expiry are worth the whole spot"
            raise PricingError(msg)
        return self.spot - pv


@dataclass(frozen=True, slots=True)
class Greeks:
    """Sensitivities per option.

    Delta and gamma are per 1 of spot; vega and rho per 1.00 (100 percentage
    points) of vol and rate; theta per year that passes.
    """

    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float


def payoff(right: Right, spot: float, strike: float) -> float:
    """max(spot - strike, 0) for a call, max(strike - spot, 0) for a put."""
    return max(spot - strike, 0.0) if right is Right.CALL else max(strike - spot, 0.0)


def norm_cdf(x: float) -> float:
    """The standard normal distribution function, from ``math.erf``."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_pdf(x: float) -> float:
    """The standard normal density."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes(o: OptionInputs) -> float:
    """The Black-Scholes price of a European option, on the escrowed spot.

    Raises:
        PricingError: The option is American (use ``price``) or the inputs are invalid.
    """
    if o.style is not ExerciseStyle.EUROPEAN:
        msg = "Black-Scholes prices European exercise only (FR-M3-05)"
        raise PricingError(msg)
    s, k, r, v, t = o.escrowed_spot(), o.strike, o.rate, o.vol, o.expiry
    if t == 0:
        return payoff(o.right, s, k)
    d1, d2 = _d1_d2(s, k, r, v, t)
    if o.right is Right.CALL:
        return s * norm_cdf(d1) - k * math.exp(-r * t) * norm_cdf(d2)
    return k * math.exp(-r * t) * norm_cdf(-d2) - s * norm_cdf(-d1)


def black_scholes_greeks(o: OptionInputs) -> Greeks:
    """Analytic Black-Scholes Greeks of a European option, on the escrowed spot.

    Raises:
        PricingError: The option is American, at expiry, or the inputs are invalid.
    """
    if o.style is not ExerciseStyle.EUROPEAN or o.expiry == 0:
        msg = "analytic Greeks need a European option before expiry"
        raise PricingError(msg)
    s, k, r, v, t = o.escrowed_spot(), o.strike, o.rate, o.vol, o.expiry
    d1, d2 = _d1_d2(s, k, r, v, t)
    discount = math.exp(-r * t)
    # The escrowed spot moves with the rate (dS*/dr = sum D t e^{-r t}) and with
    # the passing of time (dS*/d(elapsed) = -r PV), which adds delta times each.
    spot_per_rate = sum(
        d.amount * d.time * math.exp(-r * d.time) for d in o.dividends if d.time <= t
    )
    spot_per_year = -r * o.dividend_pv()
    gamma = norm_pdf(d1) / (s * v * math.sqrt(t))
    vega = s * norm_pdf(d1) * math.sqrt(t)
    decay = -s * norm_pdf(d1) * v / (2 * math.sqrt(t))
    if o.right is Right.CALL:
        delta = norm_cdf(d1)
        theta = decay - r * k * discount * norm_cdf(d2)
        rho = k * t * discount * norm_cdf(d2)
    else:
        delta = norm_cdf(d1) - 1
        theta = decay + r * k * discount * norm_cdf(-d2)
        rho = -k * t * discount * norm_cdf(-d2)
    return Greeks(
        delta=delta,
        gamma=gamma,
        vega=vega,
        theta=theta + delta * spot_per_year,
        rho=rho + delta * spot_per_rate,
    )


def binomial(o: OptionInputs, steps: int = 500) -> float:
    """The Cox-Ross-Rubinstein price, European or American, on the escrowed spot.

    The tree carries the escrowed price process. At each node, exercise is worth
    the payoff on that process plus the present value of the dividends still to
    come before expiry.

    Raises:
        PricingError: ``steps`` is below 1, the tree isn't arbitrage-free (vol at
            or below |rate| * sqrt(expiry / steps)), or the inputs are invalid.
    """
    if o.expiry == 0:
        return o.intrinsic()
    return _tree(o, steps).root


def price(o: OptionInputs, steps: int = 500) -> float:
    """The model price: Black-Scholes for European exercise, the tree for American."""
    return black_scholes(o) if o.style is ExerciseStyle.EUROPEAN else binomial(o, steps)


def greeks(o: OptionInputs, steps: int = 500) -> Greeks:
    """Greeks: analytic for European exercise, from the tree for American.

    Raises:
        PricingError: The option is at expiry, or the inputs are invalid.
    """
    if o.style is ExerciseStyle.EUROPEAN:
        return black_scholes_greeks(o)
    return tree_greeks(o, steps)


def tree_greeks(o: OptionInputs, steps: int = 500) -> Greeks:
    """Greeks from the Cox-Ross-Rubinstein tree, for either exercise style.

    Delta, gamma, and theta come from the tree's own nodes at steps 1 and 2
    (bumping the spot would shift the lattice, and its zig-zag would swamp
    gamma). Theta is corrected by delta for the real spot of the middle node at
    step 2, which differs from today's when dividends are due. Vega and rho are
    central differences of repricing.

    Raises:
        PricingError: The option is at expiry, ``steps`` is below 2, or the inputs
            are invalid.
    """
    if o.expiry == 0 or steps < 2:
        msg = "tree Greeks need an option before expiry and at least two steps"
        raise PricingError(msg)
    tree = _tree(o, steps)
    s0, up, dt = o.escrowed_spot(), tree.up, tree.dt
    (f_u, f_d), (f_uu, f_ud, f_dd) = tree.step1, tree.step2
    s_uu, s_dd = s0 * up * up, s0 / (up * up)
    delta = (f_u - f_d) / (s0 * up - s0 / up)
    gamma = ((f_uu - f_ud) / (s_uu - s0) - (f_ud - f_dd) / (s0 - s_dd)) / ((s_uu - s_dd) / 2)
    middle_spot = s0 + _pv_dividends_after(o.dividends, 2 * dt, o.expiry, o.rate)
    theta = (f_ud - tree.root - delta * (middle_spot - o.spot)) / (2 * dt)
    h_v, h_r = 1e-2, 1e-3
    vega = (
        binomial(replace(o, vol=o.vol + h_v), steps) - binomial(replace(o, vol=o.vol - h_v), steps)
    ) / (2 * h_v)
    rho = (
        binomial(replace(o, rate=o.rate + h_r), steps)
        - binomial(replace(o, rate=o.rate - h_r), steps)
    ) / (2 * h_r)
    return Greeks(delta=delta, gamma=gamma, vega=vega, theta=theta, rho=rho)


def min_tree_vol(rate: float, expiry: float, steps: int) -> float:
    """The volatility a tree of ``steps`` needs to be arbitrage-free: above |rate| * sqrt(dt)."""
    return abs(rate) * math.sqrt(expiry / steps)


def aged(o: OptionInputs, years: float) -> OptionInputs:
    """The same option ``years`` later, at the same spot.

    Expiry and every dividend come closer; the dividends paid in between drop out.

    Raises:
        PricingError: ``years`` is negative or reaches past expiry.
    """
    if not 0 <= years <= o.expiry:
        msg = f"can't age an option with {o.expiry} years left by {years}"
        raise PricingError(msg)
    return replace(
        o,
        expiry=o.expiry - years,
        dividends=tuple(Dividend(d.time - years, d.amount) for d in o.dividends if d.time >= years),
    )


def implied_vol(
    o: OptionInputs, market_price: float, steps: int = 500, tolerance: float = 1e-8
) -> float:
    """The volatility at which the model price equals ``market_price`` (bisection).

    ``o.vol`` is ignored. The search runs over vol in [1e-4, 5]; for American
    exercise, its floor is just above the lowest vol the tree can represent.

    Raises:
        PricingError: ``market_price`` is outside the prices those vols give, which
            means it breaks a no-arbitrage bound; or it equals the price at the
            lowest vol, which leaves the vol not identified.
    """
    low, high = 1e-4, 5.0
    if o.style is ExerciseStyle.AMERICAN and o.expiry > 0:
        low = max(low, min_tree_vol(o.rate, o.expiry, steps) * (1 + 1e-6))
    lowest, highest = price(replace(o, vol=low), steps), price(replace(o, vol=high), steps)
    if not lowest - tolerance <= market_price <= highest + tolerance:
        msg = (
            f"no volatility in [{low}, {high}] gives {market_price} "
            f"(the range is {lowest:.6f} to {highest:.6f})"
        )
        raise PricingError(msg)
    if market_price <= lowest + tolerance:
        msg = (
            f"{market_price} is the price at the lowest volatility ({lowest:.6f}): every "
            "lower vol gives it too (e.g. an American option worth exercising now), so "
            "the implied vol is not identified"
        )
        raise PricingError(msg)
    for _ in range(200):
        mid = (low + high) / 2
        if price(replace(o, vol=mid), steps) < market_price:
            low = mid
        else:
            high = mid
        if high - low < tolerance:
            break
    return (low + high) / 2


@dataclass(frozen=True, slots=True)
class _Tree:
    """A priced tree: its root, the values at steps 1 and 2 (highest first), its lattice."""

    root: float
    step1: tuple[float, float]
    step2: tuple[float, float, float]
    up: float
    dt: float


def _tree(o: OptionInputs, steps: int) -> _Tree:
    if steps < 1:
        msg = "the tree needs at least one step"
        raise PricingError(msg)
    s0, k, r, v, t = o.escrowed_spot(), o.strike, o.rate, o.vol, o.expiry
    dt = t / steps
    if v <= min_tree_vol(r, t, steps):
        msg = f"vol {v} is too low for an arbitrage-free tree of {steps} steps"
        raise PricingError(msg)
    up = math.exp(v * math.sqrt(dt))
    down = 1 / up
    grow = math.exp(r * dt)
    p = (grow - down) / (up - down)
    disc = 1 / grow
    american = o.style is ExerciseStyle.AMERICAN
    values = [payoff(o.right, s0 * up ** (steps - 2 * j), k) for j in range(steps + 1)]
    kept: dict[int, tuple[float, ...]] = {steps: tuple(values[:3])}
    for step in range(steps - 1, -1, -1):
        remaining = _pv_dividends_after(o.dividends, step * dt, t, r)
        for j in range(step + 1):
            held = disc * (p * values[j] + (1 - p) * values[j + 1])
            if american:
                spot = s0 * up ** (step - 2 * j) + remaining
                held = max(held, payoff(o.right, spot, k))
            values[j] = held
        if step <= 2:
            kept[step] = tuple(values[: step + 1])
    step1 = kept.get(1, (math.nan, math.nan))
    step2 = kept.get(2, (math.nan, math.nan, math.nan))
    return _Tree(
        root=values[0],
        step1=(step1[0], step1[1]),
        step2=(step2[0], step2[1], step2[2]),
        up=up,
        dt=dt,
    )


def _d1_d2(s: float, k: float, r: float, v: float, t: float) -> tuple[float, float]:
    d1 = (math.log(s / k) + (r + v * v / 2) * t) / (v * math.sqrt(t))
    return d1, d1 - v * math.sqrt(t)


def _pv_dividends_after(
    dividends: Sequence[Dividend], now: float, expiry: float, rate: float
) -> float:
    """At time ``now``, the present value of the dividends paid after it and by expiry.

    At ``now == 0`` a dividend at time 0 counts: it hasn't been paid yet.
    """
    return sum(
        d.amount * math.exp(-rate * (d.time - now))
        for d in dividends
        if (now < d.time or d.time == now == 0) and d.time <= expiry
    )
