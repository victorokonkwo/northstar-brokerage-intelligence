"""The XNYS and XCBO calendars against the published 2026 schedules."""

from datetime import date, timedelta

import pytest

from northstar.common.exceptions import CalendarCoverageError, PolicyNotFoundError
from northstar.domain.calendar import MarketCalendar
from northstar.domain.settlement import AssetClass, SettlementRules, settlement_date
from northstar.infrastructure.config_loader import load_policy

from factories import CONFIG


@pytest.fixture(scope="module")
def xnys() -> MarketCalendar:
    return load_policy(CONFIG / "calendars" / "xnys.yaml", MarketCalendar)


@pytest.fixture(scope="module")
def xcbo() -> MarketCalendar:
    return load_policy(CONFIG / "calendars" / "xcbo.yaml", MarketCalendar)


@pytest.fixture(scope="module")
def rules() -> SettlementRules:
    return load_policy(CONFIG / "brokerage" / "settlement_rules.yaml", SettlementRules)


# T+1 settlement around each 2026 non-settlement day. The Good Friday,
# Memorial Day, and Independence Day rows restate the NSCC notices (A#9732,
# A#9755, A#9778), which name the next settlement date. The Columbus and
# Veterans Day rows follow from DTC 23036-25 ("open but will not provide
# settlement services"). The others follow from DTC listing the day "Closed".
US_EQUITY_T_PLUS_1 = [
    (date(2026, 1, 16), date(2026, 1, 20)),  # MLK Day
    (date(2026, 2, 13), date(2026, 2, 17)),  # Presidents' Day
    (date(2026, 4, 2), date(2026, 4, 6)),  # Good Friday: NSCC closed
    (date(2026, 5, 22), date(2026, 5, 26)),  # Memorial Day
    (date(2026, 6, 18), date(2026, 6, 22)),  # Juneteenth
    (date(2026, 7, 2), date(2026, 7, 6)),  # Independence Day observed: NSCC closed
    (date(2026, 9, 4), date(2026, 9, 8)),  # Labor Day
    (date(2026, 10, 9), date(2026, 10, 13)),  # Columbus Day: DTC no settlement
    (date(2026, 11, 10), date(2026, 11, 12)),  # Veterans Day: DTC no settlement
    (date(2026, 11, 25), date(2026, 11, 27)),  # Thanksgiving
    (date(2026, 12, 24), date(2026, 12, 28)),  # Christmas
]


class TestXnys:
    @pytest.mark.parametrize(("trade_date", "expected"), US_EQUITY_T_PLUS_1)
    def test_t_plus_one_skips_every_us_settlement_holiday(
        self, xnys: MarketCalendar, rules: SettlementRules, trade_date: date, expected: date
    ) -> None:
        assert settlement_date(trade_date, "XNYS", AssetClass.EQUITY, rules, xnys) == expected

    def test_bank_only_holidays_trade_but_do_not_settle(self, xnys: MarketCalendar) -> None:
        for day in (date(2026, 10, 12), date(2026, 11, 11)):
            assert xnys.is_trading_day(day)
            assert not xnys.is_settlement_day(day)

    def test_early_closes_are_trading_and_settlement_days(self, xnys: MarketCalendar) -> None:
        for day in (date(2026, 11, 27), date(2026, 12, 24)):
            assert xnys.is_trading_day(day)
            assert xnys.is_settlement_day(day)

    def test_day_counts_for_2026(self, xnys: MarketCalendar) -> None:
        days = [date(2026, 1, 1) + timedelta(days=n) for n in range(365)]
        assert sum(xnys.is_trading_day(d) for d in days) == 261 - 10
        assert sum(xnys.is_settlement_day(d) for d in days) == 261 - 12

    def test_a_trade_settling_in_2027_is_refused(
        self, xnys: MarketCalendar, rules: SettlementRules
    ) -> None:
        with pytest.raises(CalendarCoverageError):
            settlement_date(date(2026, 12, 31), "XNYS", AssetClass.EQUITY, rules, xnys)

    def test_us_t_plus_one_starts_on_2024_05_28(self, rules: SettlementRules) -> None:
        assert rules.cycle_for("XNYS", AssetClass.EQUITY, date(2024, 5, 28)).business_days == 1
        with pytest.raises(PolicyNotFoundError):
            rules.cycle_for("XNYS", AssetClass.EQUITY, date(2024, 5, 24))


class TestXcbo:
    @pytest.mark.parametrize(
        ("trade_date", "expected"),
        [
            (date(2026, 1, 2), date(2026, 1, 5)),
            (date(2026, 1, 16), date(2026, 1, 20)),  # MLK Day
            (date(2026, 2, 13), date(2026, 2, 17)),  # Presidents' Day
            (date(2026, 3, 31), date(2026, 4, 1)),
        ],
    )
    def test_option_premium_settles_t_plus_one(
        self, xcbo: MarketCalendar, rules: SettlementRules, trade_date: date, expected: date
    ) -> None:
        assert settlement_date(trade_date, "XCBO", AssetClass.OPTION, rules, xcbo) == expected

    def test_coverage_stops_before_the_unverified_period(
        self, xcbo: MarketCalendar, rules: SettlementRules
    ) -> None:
        # 2026-04-02 + 1 settlement day would cross Good Friday into April 6,
        # outside the verified window.
        with pytest.raises(CalendarCoverageError):
            settlement_date(date(2026, 4, 2), "XCBO", AssetClass.OPTION, rules, xcbo)
        with pytest.raises(CalendarCoverageError):
            xcbo.is_trading_day(date(2026, 10, 12))

    def test_options_cycle_is_not_applied_to_equities(self, rules: SettlementRules) -> None:
        with pytest.raises(PolicyNotFoundError):
            rules.cycle_for("XCBO", AssetClass.EQUITY, date(2026, 3, 2))
