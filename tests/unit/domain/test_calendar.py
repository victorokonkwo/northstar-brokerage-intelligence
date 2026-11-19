"""Tests for ``northstar.domain.calendar`` against the committed XTSE calendar."""

from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from northstar.common.exceptions import CalendarCoverageError
from northstar.domain.calendar import MarketCalendar, business_date
from northstar.infrastructure.config_loader import load_policy

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def xtse() -> MarketCalendar:
    return load_policy(REPO_ROOT / "config" / "calendars" / "xtse.yaml", MarketCalendar)


# Every trade-date -> settlement-date row for CAD-traded securities in the
# TMX "Settlement Schedule for 2026 Holidays" and "... 2027 Holidays" PDFs
# (retrieved 2026-11-19; docs/sources.md -> Calendars). Under T+1.
TMX_SETTLEMENT_SCHEDULE = [
    (date(2026, 2, 13), date(2026, 2, 17)),  # Family Day
    (date(2026, 4, 2), date(2026, 4, 6)),  # Good Friday
    (date(2026, 5, 15), date(2026, 5, 19)),  # Victoria Day
    (date(2026, 6, 30), date(2026, 7, 2)),  # Canada Day
    (date(2026, 7, 31), date(2026, 8, 4)),  # Civic Holiday
    (date(2026, 9, 4), date(2026, 9, 8)),  # Labour Day
    (date(2026, 9, 29), date(2026, 10, 1)),  # Truth and Reconciliation (banking)
    (date(2026, 10, 9), date(2026, 10, 13)),  # Thanksgiving
    (date(2026, 11, 10), date(2026, 11, 12)),  # Remembrance Day (banking)
    (date(2026, 12, 24), date(2026, 12, 29)),  # Christmas and Boxing Day
    (date(2026, 12, 31), date(2027, 1, 4)),  # New Year's Day 2027
]


@pytest.mark.parametrize(("trade_date", "expected"), TMX_SETTLEMENT_SCHEDULE)
def test_t_plus_one_matches_the_published_tmx_settlement_schedule(
    xtse: MarketCalendar, trade_date: date, expected: date
) -> None:
    assert xtse.add_settlement_days(trade_date, 1) == expected


def test_banking_holidays_trade_but_do_not_settle(xtse: MarketCalendar) -> None:
    for day in (date(2026, 9, 30), date(2026, 11, 11)):
        assert xtse.is_trading_day(day)
        assert not xtse.is_settlement_day(day)


def test_christmas_eve_early_close_is_a_trading_and_settlement_day(xtse: MarketCalendar) -> None:
    assert xtse.is_trading_day(date(2026, 12, 24))
    assert xtse.is_settlement_day(date(2026, 12, 24))


def test_us_holidays_do_not_affect_cad_settlement(xtse: MarketCalendar) -> None:
    # Martin Luther King Jr. Day 2026: the TMX schedule shifts USD trades only.
    assert xtse.is_settlement_day(date(2026, 1, 19))
    assert xtse.add_settlement_days(date(2026, 1, 16), 1) == date(2026, 1, 19)


def test_weekends_are_never_trading_or_settlement_days(xtse: MarketCalendar) -> None:
    saturday, sunday = date(2026, 3, 7), date(2026, 3, 8)
    for day in (saturday, sunday):
        assert not xtse.is_trading_day(day)
        assert not xtse.is_settlement_day(day)


def test_trading_and_settlement_day_counts_for_2026(xtse: MarketCalendar) -> None:
    days = [date(2026, 1, 1) + timedelta(days=n) for n in range(365)]
    weekdays = sum(d.weekday() < 5 for d in days)
    assert weekdays == 261
    assert sum(xtse.is_trading_day(d) for d in days) == 261 - 10
    assert sum(xtse.is_settlement_day(d) for d in days) == 261 - 12


def test_settlement_day_on_or_after(xtse: MarketCalendar) -> None:
    assert xtse.settlement_day_on_or_after(date(2026, 3, 9)) == date(2026, 3, 9)
    assert xtse.settlement_day_on_or_after(date(2026, 3, 7)) == date(2026, 3, 9)
    assert xtse.settlement_day_on_or_after(date(2026, 12, 25)) == date(2026, 12, 29)


@pytest.mark.parametrize("outside", [date(2025, 12, 31), date(2027, 2, 1)])
def test_dates_outside_coverage_are_refused(xtse: MarketCalendar, outside: date) -> None:
    with pytest.raises(CalendarCoverageError, match="outside the XTSE calendar coverage"):
        xtse.is_trading_day(outside)
    with pytest.raises(CalendarCoverageError):
        xtse.is_settlement_day(outside)


def test_walking_past_the_coverage_end_is_refused(xtse: MarketCalendar) -> None:
    # Friday 2027-01-29 + 1 settlement day would be Monday 2027-02-01.
    with pytest.raises(CalendarCoverageError):
        xtse.add_settlement_days(date(2027, 1, 29), 1)


def test_add_settlement_days_requires_a_positive_count(xtse: MarketCalendar) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        xtse.add_settlement_days(date(2026, 3, 9), 0)


class TestValidation:
    @staticmethod
    def _calendar(**overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "market": "TEST",
            "settlement_currency": "CAD",
            "version": "t",
            "effective_from": date(2026, 1, 1),
            "coverage": {"start": date(2026, 1, 1), "end": date(2026, 12, 31)},
            "sources": {
                "s": {"title": "t", "url": "https://example.invalid", "retrieved": date(2026, 1, 1)}
            },
            "trading_holidays": [],
            "settlement_holidays": [],
        }
        return base | overrides

    def test_minimal_calendar_is_valid(self) -> None:
        MarketCalendar.model_validate(self._calendar())

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"coverage": {"start": date(2026, 2, 1), "end": date(2026, 1, 1)}}, "after end"),
            (
                {"trading_holidays": [{"date": date(2027, 1, 4), "name": "x", "source": "s"}]},
                "outside the coverage",
            ),
            (
                {"trading_holidays": [{"date": date(2026, 3, 7), "name": "x", "source": "s"}]},
                "weekend",
            ),
            (
                {
                    "settlement_holidays": [
                        {"date": date(2026, 3, 9), "name": "x", "source": "s"},
                        {"date": date(2026, 3, 9), "name": "y", "source": "s"},
                    ]
                },
                "more than once",
            ),
            (
                {"trading_holidays": [{"date": date(2026, 3, 9), "name": "x", "source": "nope"}]},
                "unknown source",
            ),
            ({"unexpected_field": 1}, "Extra inputs"),
        ],
    )
    def test_invalid_calendars_are_rejected(self, overrides: dict[str, Any], message: str) -> None:
        with pytest.raises(ValidationError, match=message):
            MarketCalendar.model_validate(self._calendar(**overrides))


class TestBusinessDate:
    @pytest.mark.parametrize(
        ("utc_timestamp", "expected"),
        [
            # Winter (EST, UTC-5): Toronto midnight is 05:00 UTC.
            (datetime(2026, 1, 15, 4, 59, tzinfo=UTC), date(2026, 1, 14)),
            (datetime(2026, 1, 15, 5, 0, tzinfo=UTC), date(2026, 1, 15)),
            # Summer (EDT, UTC-4), after the 2026-03-08 change: midnight is 04:00 UTC.
            (datetime(2026, 3, 9, 3, 59, tzinfo=UTC), date(2026, 3, 8)),
            (datetime(2026, 3, 9, 4, 0, tzinfo=UTC), date(2026, 3, 9)),
            # Back to EST after 2026-11-01.
            (datetime(2026, 11, 2, 4, 30, tzinfo=UTC), date(2026, 11, 1)),
            (datetime(2026, 11, 2, 5, 0, tzinfo=UTC), date(2026, 11, 2)),
        ],
    )
    def test_business_date_is_the_toronto_calendar_date(
        self, utc_timestamp: datetime, expected: date
    ) -> None:
        assert business_date(utc_timestamp) == expected

    def test_any_aware_timezone_is_converted(self) -> None:
        tokyo = timezone(timedelta(hours=9))
        assert business_date(datetime(2026, 1, 15, 18, 0, tzinfo=tokyo)) == date(2026, 1, 15)
        assert business_date(datetime(2026, 1, 15, 8, 0, tzinfo=tokyo)) == date(2026, 1, 14)

    def test_naive_timestamps_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            business_date(datetime(2026, 1, 15, 12, 0))  # noqa: DTZ001 - the case under test


class TestPreviousSettlementDay:
    def test_skips_weekends_and_settlement_holidays(self, xtse: MarketCalendar) -> None:
        cal = xtse
        assert cal.previous_settlement_day(date(2026, 3, 2)) == date(2026, 2, 27)  # Mon -> Fri
        # Remembrance Day (2026-11-11) is a CAD settlement holiday, not a trading holiday.
        assert cal.previous_settlement_day(date(2026, 11, 12)) == date(2026, 11, 10)

    def test_leaving_coverage_is_an_error(self, xtse: MarketCalendar) -> None:
        with pytest.raises(CalendarCoverageError):
            xtse.previous_settlement_day(date(2026, 1, 2))  # the walk passes 2026-01-01
