"""The committed risk policies load, and their validators refuse what they should."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from northstar.common.exceptions import ConfigurationError
from northstar.domain.market_risk.policy import (
    MarginPolicies,
    RiskFreeRates,
    RiskLimits,
    StressScenarios,
    VarMethod,
)
from northstar.domain.settlement import AssetClass
from northstar.infrastructure.config_loader import load_policy

from factories import CONFIG

RISK = CONFIG / "risk"


def test_the_committed_policies_load() -> None:
    margin = load_policy(RISK / "margin_policies.yaml", MarginPolicies)
    stress = load_policy(RISK / "stress_scenarios.yaml", StressScenarios)
    load_policy(RISK / "risk_limits.yaml", RiskLimits)
    rates = load_policy(RISK / "rates.yaml", RiskFreeRates)
    method = load_policy(RISK / "var_method.yaml", VarMethod)
    assert margin.rates_for(AssetClass.EQUITY, "SYNA").requirement_rate == Decimal("0.30")
    assert margin.rates_for(AssetClass.EQUITY, "SYNH").haircut == Decimal("0.50")  # override
    assert margin.rates_for(AssetClass.OPTION, "SYNB-C").haircut == Decimal("1.00")
    assert stress.scenarios[0].name == "ZERO"
    assert next(s for s in stress.scenarios if s.name == "SINGLE_SYNH_30").shock_for(
        "SYNA"
    ) == Decimal(0)
    assert rates.continuous_rates
    # OQ-08: 250 scenarios, VaR 99% is the 3rd worst loss, ES 97.5% the mean of the 7 worst.
    assert (method.window_days, method.var_rank, method.es_count) == (250, 3, 7)


def test_a_missing_asset_class_is_refused() -> None:
    with pytest.raises(ValidationError, match="no margin rates"):
        MarginPolicies.model_validate(
            {
                "version": "x",
                "effective_from": "2026-01-01",
                "illustrative": True,
                "by_asset_class": {"EQUITY": {"requirement_rate": "0.3", "haircut": "0.3"}},
            }
        )


@pytest.mark.parametrize(
    "scenarios",
    [
        [{"name": "EQ_DOWN", "equity_shock": "-0.1", "vol_multiplier": "1"}],  # no ZERO
        [{"name": "ZERO", "equity_shock": "-0.1", "vol_multiplier": "1"}],  # ZERO with a shock
        [
            {"name": "ZERO", "equity_shock": "0", "vol_multiplier": "1"},
            {"name": "ZERO", "equity_shock": "0", "vol_multiplier": "1"},
        ],  # repeated name
    ],
)
def test_the_scenario_set_needs_one_true_zero(scenarios: list[dict[str, str]]) -> None:
    with pytest.raises(ValidationError):
        StressScenarios.model_validate(
            {"version": "x", "effective_from": "2026-01-01", "illustrative": True,
             "scenarios": scenarios}
        )  # fmt: skip


def test_rates_outside_zero_and_one_are_refused() -> None:
    with pytest.raises(ValidationError):
        MarginPolicies.model_validate(
            {
                "version": "x",
                "effective_from": "2026-01-01",
                "illustrative": True,
                "by_asset_class": {
                    "EQUITY": {"requirement_rate": "1.3", "haircut": "0.3"},
                    "OPTION": {"requirement_rate": "1", "haircut": "1"},
                },
            }
        )


def test_order_statistics_follow_the_window() -> None:
    method = VarMethod.model_validate(
        {"model_version": "m", "effective_from": "2026-01-01", "window_days": 500,
         "horizon_days": 1, "var_confidence": "0.99", "es_confidence": "0.975"}
    )  # fmt: skip
    assert (method.var_rank, method.es_count) == (5, 13)


def test_a_config_that_isnt_a_policy_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError):
        load_policy(RISK / "rates.yaml", VarMethod)
