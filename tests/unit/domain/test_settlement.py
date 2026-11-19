"""Tests for ``northstar.domain.settlement``."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from northstar.common.exceptions import NotATradingDayError, PolicyNotFoundError
from northstar.domain.calendar import MarketCalendar
from northstar.domain.settlement import AssetClass, SettlementRules, settlement_date
from northstar.infrastructure.config_loader import load_policy

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def xtse() -> MarketCalendar:
    return load_policy(REPO_ROOT / "config" / "calendars" / "xtse.yaml", MarketCalendar)


@pytest.fixture(scope="module")
def rules() -> SettlementRules:
    return load_policy(
        REPO_ROOT / "config" / "brokerage" / "settlement_rules.yaml", SettlementRules
    )


def _cycle(business_days: int, effective_from: date, market: str = "TEST") -> dict[str, Any]:
    return {
        "market": market,
        "asset_classes": ["EQUITY"],
        "business_days": business_days,
        "effective_from": effective_from,
        "source": "test fixture",
    }


# A weekends-only calendar: a test fixture for rule selection, not market data.
WEEKENDS_ONLY = MarketCalendar.model_validate(
    {
        "market": "TEST",
        "settlement_currency": "CAD",
        "version": "fixture",
        "effective_from": date(2020, 1, 1),
        "coverage": {"start": date(2020, 1, 1), "end": date(2030, 12, 31)},
        "sources": {},
        "trading_holidays": [],
        "settlement_holidays": [],
    }
)


class TestCommittedPolicy:
    def test_xtse_equity_is_t_plus_one(self, xtse: MarketCalendar, rules: SettlementRules) -> None:
        # Tuesday trade, Wednesday 2026-11-11 is Remembrance Day (no CAD settlement).
        assert settlement_date(date(2026, 11, 10), "XTSE", AssetClass.EQUITY, rules, xtse) == date(
            2026, 11, 12
        )
        assert settlement_date(date(2026, 3, 9), "XTSE", AssetClass.EQUITY, rules, xtse) == date(
            2026, 3, 10
        )

    def test_trade_on_a_market_holiday_is_rejected(
        self, xtse: MarketCalendar, rules: SettlementRules
    ) -> None:
        with pytest.raises(NotATradingDayError, match="2026-02-16"):
            settlement_date(date(2026, 2, 16), "XTSE", AssetClass.EQUITY, rules, xtse)

    def test_calendar_for_another_market_is_rejected(
        self, xtse: MarketCalendar, rules: SettlementRules
    ) -> None:
        with pytest.raises(ValueError, match="calendar is for XTSE"):
            settlement_date(date(2026, 3, 9), "XNYS", AssetClass.EQUITY, rules, xtse)

    def test_no_unverified_cycle_is_assumed_before_t_plus_one(self, rules: SettlementRules) -> None:
        # The pre-2024-05-27 cycle is not verified, so it is not in the policy.
        with pytest.raises(PolicyNotFoundError, match="2024-05-24"):
            rules.cycle_for("XTSE", AssetClass.EQUITY, date(2024, 5, 24))
        assert rules.cycle_for("XTSE", AssetClass.EQUITY, date(2024, 5, 27)).business_days == 1


class TestRuleSelection:
    RULES = SettlementRules.model_validate(
        {
            "version": "fixture",
            "effective_from": date(2020, 1, 1),
            "cycles": [_cycle(2, date(2020, 1, 1)), _cycle(1, date(2026, 6, 1))],
        }
    )

    @pytest.mark.parametrize(
        ("trade_date", "expected"),
        [
            (date(2026, 5, 29), date(2026, 6, 2)),  # Friday under T+2
            (date(2026, 6, 1), date(2026, 6, 2)),  # Monday, first T+1 trade date
            (date(2026, 6, 5), date(2026, 6, 8)),  # Friday under T+1
        ],
    )
    def test_cycle_in_force_on_the_trade_date_is_used(
        self, trade_date: date, expected: date
    ) -> None:
        assert (
            settlement_date(trade_date, "TEST", AssetClass.EQUITY, self.RULES, WEEKENDS_ONLY)
            == expected
        )

    def test_before_the_first_cycle_nothing_applies(self) -> None:
        with pytest.raises(PolicyNotFoundError):
            self.RULES.cycle_for("TEST", AssetClass.EQUITY, date(2019, 12, 31))

    def test_unknown_market_has_no_cycle(self) -> None:
        with pytest.raises(PolicyNotFoundError, match="OTHER"):
            self.RULES.cycle_for("OTHER", AssetClass.EQUITY, date(2026, 6, 1))

    def test_conflicting_cycles_are_rejected(self) -> None:
        with pytest.raises(ValidationError, match="two cycles start"):
            SettlementRules.model_validate(
                {
                    "version": "x",
                    "effective_from": date(2020, 1, 1),
                    "cycles": [_cycle(1, date(2020, 1, 1)), _cycle(2, date(2020, 1, 1))],
                }
            )

    def test_zero_day_cycle_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SettlementRules.model_validate(
                {
                    "version": "x",
                    "effective_from": date(2020, 1, 1),
                    "cycles": [_cycle(0, date(2020, 1, 1))],
                }
            )
