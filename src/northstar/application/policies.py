"""Load every brokerage policy version from the config directory (W2: no constants)."""

from pathlib import Path

from northstar.domain.accounting import ChartOfAccounts
from northstar.domain.calendar import MarketCalendar
from northstar.domain.corporate_actions import OptionExpiryPolicy
from northstar.domain.fx import FxPolicy
from northstar.domain.interest import MarginPolicy
from northstar.domain.market_risk.policy import RiskFreeRates
from northstar.domain.settlement import SettlementRules
from northstar.domain.trading import FeeSchedule
from northstar.infrastructure.config_loader import load_policy
from northstar.simulation.engine import Policies

CALENDAR_FILES = ("xtse", "xnys", "xcbo")
"""Market calendars under ``config/calendars/``."""


def load_policies(config_dir: Path) -> Policies:
    """Load calendars, settlement rules, fees, FX, margin, the chart, and risk-free rates.

    Raises:
        ConfigurationError: A policy file is missing or invalid.
    """
    calendars = {}
    for name in CALENDAR_FILES:
        calendar = load_policy(config_dir / "calendars" / f"{name}.yaml", MarketCalendar)
        calendars[calendar.market] = calendar
    brokerage = config_dir / "brokerage"
    return Policies(
        calendars=calendars,
        settlement_rules=load_policy(brokerage / "settlement_rules.yaml", SettlementRules),
        fee_schedule=load_policy(brokerage / "fee_schedule.yaml", FeeSchedule),
        fx_policy=load_policy(brokerage / "fx_policy.yaml", FxPolicy),
        margin_policy=load_policy(brokerage / "margin_policy.yaml", MarginPolicy),
        option_expiry=load_policy(brokerage / "option_expiry_policy.yaml", OptionExpiryPolicy),
        chart=load_policy(config_dir / "accounting" / "chart_of_accounts.yaml", ChartOfAccounts),
        risk_free_rates=load_policy(config_dir / "risk" / "rates.yaml", RiskFreeRates),
    )
