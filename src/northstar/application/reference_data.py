"""Reference data the warehouse needs from configuration (calendars).

The calendar logic has one owner, ``northstar.domain.calendar`` (ADR 007):
this module asks it for every covered date, and dbt only reshapes the result
into ``dim_calendar``.
"""

from collections.abc import Mapping
from datetime import timedelta

from northstar.common.exceptions import CalendarCoverageError
from northstar.domain.calendar import MarketCalendar
from northstar.infrastructure.postgres import CalendarDay


def calendar_days(calendars: Mapping[str, MarketCalendar]) -> list[CalendarDay]:
    """Every covered date of every calendar, with its flags and neighbouring settlement days."""
    days = []
    for market, calendar in sorted(calendars.items()):
        trading = {h.date: h.name for h in calendar.trading_holidays}
        settlement = {h.date: h.name for h in calendar.settlement_holidays}
        day = calendar.coverage.start
        while day <= calendar.coverage.end:
            try:
                previous = calendar.previous_settlement_day(day)
            except CalendarCoverageError:
                previous = None
            try:
                following = calendar.add_settlement_days(day, 1)
            except CalendarCoverageError:
                following = None
            days.append(
                CalendarDay(
                    market=market,
                    calendar_date=day,
                    is_trading_day=calendar.is_trading_day(day),
                    is_settlement_day=calendar.is_settlement_day(day),
                    trading_holiday_name=trading.get(day),
                    settlement_holiday_name=settlement.get(day),
                    previous_settlement_day=previous,
                    next_settlement_day=following,
                    calendar_version=calendar.version,
                )
            )
            day += timedelta(days=1)
    return days
