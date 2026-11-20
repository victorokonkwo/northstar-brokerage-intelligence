"""Synthetic option closes are model prices on the underlying (roadmap 5B; ADR 012)."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from northstar.common.money import Currency
from northstar.domain.calendar import Holiday
from northstar.domain.events import ExerciseStyle, OptionRight
from northstar.domain.market_risk.options import OptionInputs, binomial, implied_vol
from northstar.infrastructure.config_loader import load_policy
from northstar.simulation.generator import (
    DatasetConfig,
    GeneratedInstrument,
    MarketData,
    build_market,
    market_stream,
)
from northstar.simulation.market import OPTION_TREE_STEPS, model_option_close

from factories import CONFIG, policies

DEV = CONFIG / "simulation" / "datasets" / "dev.yaml"
USD_RATE = policies().risk_free_rates.continuous_rates[Currency.USD]  # the dev options are USD


@pytest.fixture(scope="module")
def config() -> DatasetConfig:
    return load_policy(DEV, DatasetConfig)


@pytest.fixture(scope="module")
def market(config: DatasetConfig) -> MarketData:
    p = policies()
    return build_market(config, p.calendars, p.risk_free_rates, market_stream(42))


def _options(config: DatasetConfig) -> list[GeneratedInstrument]:
    options = [s for s in config.instruments if s.option is not None]
    assert options, "the dev dataset lists options"
    return options


def _inputs(spec: GeneratedInstrument, spot: Decimal, day: date, rate: Decimal) -> OptionInputs:
    assert spec.option is not None
    assert spec.implied_vol is not None
    return OptionInputs(
        right=spec.option.right,
        style=spec.option.exercise_style,
        spot=float(spot),
        strike=float(spec.option.strike),
        rate=float(rate),
        vol=float(spec.implied_vol),
        expiry=(spec.option.expiry - day).days / 365,
    )


def test_each_close_is_the_tree_price_on_the_underlyings_close(
    config: DatasetConfig, market: MarketData
) -> None:
    usd = USD_RATE
    for spec in _options(config):
        assert spec.option is not None
        underlying = market.closes[spec.option.underlying]
        for day, close in market.closes[spec.key].items():
            value = binomial(_inputs(spec, underlying[day], day, usd), OPTION_TREE_STEPS)
            assert close == max(round(Decimal(value), 2), Decimal("0.01")), (spec.key, day)


def test_the_risk_model_recovers_the_configured_implied_vol(
    config: DatasetConfig, market: MarketData
) -> None:
    for spec in _options(config):
        assert spec.option is not None
        assert spec.implied_vol is not None
        underlying = market.closes[spec.option.underlying]
        for day, close in list(market.closes[spec.key].items())[::10]:  # bisection is slow
            o = _inputs(spec, underlying[day], day, USD_RATE)
            recovered = implied_vol(o, float(close), steps=OPTION_TREE_STEPS)
            # Up to the rounding of the close to a cent: half a cent over vega.
            assert recovered == pytest.approx(float(spec.implied_vol), abs=2e-3), (spec.key, day)


def test_an_option_has_no_close_on_a_day_its_underlying_has_none(
    config: DatasetConfig,
) -> None:
    p = policies()
    closed = date(2026, 2, 3)  # an ordinary Tuesday, closed here for XNYS only
    xnys = p.calendars["XNYS"]
    holiday = Holiday(date=closed, name="test closure", source=next(iter(xnys.sources)))
    raw = xnys.model_dump() | {"trading_holidays": (*xnys.trading_holidays, holiday)}
    calendars = p.calendars | {"XNYS": type(xnys).model_validate(raw)}
    market = build_market(config, calendars, p.risk_free_rates, market_stream(42))
    assert calendars["XCBO"].is_trading_day(closed)
    for spec in _options(config):
        assert closed not in market.closes[spec.key]  # not carried forward
        assert date(2026, 2, 4) in market.closes[spec.key]


def test_an_options_opening_price_is_its_model_price_before_the_window(
    config: DatasetConfig, market: MarketData
) -> None:
    for spec in _options(config):
        assert spec.option is not None
        before = date(2026, 1, 1)
        spot = market.opening[spec.option.underlying]
        value = binomial(_inputs(spec, spot, before, USD_RATE), OPTION_TREE_STEPS)
        assert market.opening[spec.key] == round(Decimal(value), 2)


def test_a_close_never_falls_below_a_cent_or_prices_past_expiry() -> None:
    def close(day: date) -> Decimal:
        return model_option_close(
            OptionRight.CALL,
            ExerciseStyle.AMERICAN,
            Decimal("500.00"),
            date(2026, 3, 20),
            Decimal("10.00"),
            day,
            Decimal("0.04"),
            Decimal("0.20"),
        )

    assert close(date(2026, 3, 2)) == Decimal("0.01")  # worth far less than a cent
    with pytest.raises(ValueError, match="expired"):
        close(date(2026, 3, 23))


@pytest.mark.parametrize(
    ("changes", "valid"),
    [
        ({}, True),
        ({"implied_vol": None}, False),
        ({"start_price": "2.15", "daily_vol": "0.06"}, False),
    ],
)
def test_an_option_is_configured_by_its_implied_vol_only(
    config: DatasetConfig, changes: dict[str, str | None], valid: bool
) -> None:
    raw = _options(config)[0].model_dump() | changes
    if valid:
        GeneratedInstrument.model_validate(raw)
    else:
        with pytest.raises(ValidationError, match="implied_vol only"):
            GeneratedInstrument.model_validate(raw)


def test_an_equity_needs_its_walk_and_no_implied_vol(config: DatasetConfig) -> None:
    equity = next(s for s in config.instruments if s.option is None).model_dump()
    with pytest.raises(ValidationError, match="implied_vol only"):
        GeneratedInstrument.model_validate(equity | {"implied_vol": "0.2"})
    with pytest.raises(ValidationError, match="implied_vol only"):
        GeneratedInstrument.model_validate(equity | {"daily_vol": None})
