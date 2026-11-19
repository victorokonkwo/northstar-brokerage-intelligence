"""Business dates and market calendars (domain model §5, OQ-04).

A ``MarketCalendar`` knows two different kinds of non-business day:

- **Trading holidays:** the exchange is closed. Executions on these days are
  rejected.
- **Settlement holidays:** securities traded on the exchange do not settle.
  This set can contain trading days. For example, on Remembrance Day the TSX
  trades but CAD-traded securities do not settle.

Weekends are never trading or settlement days. A calendar only answers for
dates inside its verified coverage window. Outside it, the holidays are
unknown, so it raises ``CalendarCoverageError`` instead of guessing.
"""

from datetime import date, datetime, timedelta
from typing import Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, PrivateAttr, model_validator

from northstar.common.exceptions import CalendarCoverageError

BUSINESS_TIMEZONE = ZoneInfo("America/Toronto")
"""Timezone that defines the business date (domain model §5)."""

_SATURDAY = 5


def business_date(timestamp: datetime) -> date:
    """Return the business date of ``timestamp``: its calendar date in Toronto.

    Raises:
        ValueError: ``timestamp`` is naive (has no timezone).
    """
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        msg = f"timestamp must be timezone-aware, got naive {timestamp.isoformat()}"
        raise ValueError(msg)
    return timestamp.astimezone(BUSINESS_TIMEZONE).date()


class SourceReference(BaseModel):
    """Where a set of calendar facts was verified."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    url: str
    retrieved: date


class Holiday(BaseModel):
    """One dated closure, with the key of the source that states it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: date
    name: str
    source: str


class Coverage(BaseModel):
    """Inclusive date range for which the calendar's holidays are verified."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: date
    end: date


class MarketCalendar(BaseModel):
    """Trading and settlement calendar for one market, versioned as policy.

    Raises:
        pydantic.ValidationError: On construction, if the coverage window is
            empty, a holiday falls outside it or on a weekend, a date is
            listed twice, or a holiday cites an unknown source.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    settlement_currency: str
    version: str
    effective_from: date
    coverage: Coverage
    sources: dict[str, SourceReference]
    trading_holidays: tuple[Holiday, ...]
    settlement_holidays: tuple[Holiday, ...]

    _trading_closed: frozenset[date] = PrivateAttr()
    _settlement_closed: frozenset[date] = PrivateAttr()

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.coverage.start > self.coverage.end:
            msg = f"coverage start {self.coverage.start} is after end {self.coverage.end}"
            raise ValueError(msg)
        for kind, holidays in (
            ("trading", self.trading_holidays),
            ("settlement", self.settlement_holidays),
        ):
            dates = [h.date for h in holidays]
            if len(dates) != len(set(dates)):
                msg = f"{kind} holidays list a date more than once"
                raise ValueError(msg)
            for holiday in holidays:
                if not self.coverage.start <= holiday.date <= self.coverage.end:
                    msg = f"{kind} holiday {holiday.date} is outside the coverage window"
                    raise ValueError(msg)
                if holiday.date.weekday() >= _SATURDAY:
                    msg = f"{kind} holiday {holiday.date} falls on a weekend"
                    raise ValueError(msg)
                if holiday.source not in self.sources:
                    msg = f"{kind} holiday {holiday.date} cites unknown source {holiday.source!r}"
                    raise ValueError(msg)
        return self

    def model_post_init(self, context: object, /) -> None:
        """Index the holiday dates for constant-time lookups."""
        self._trading_closed = frozenset(h.date for h in self.trading_holidays)
        self._settlement_closed = frozenset(h.date for h in self.settlement_holidays)

    def is_trading_day(self, day: date) -> bool:
        """Whether the exchange is open on ``day``.

        Raises:
            CalendarCoverageError: ``day`` is outside the coverage window.
        """
        self._require_covered(day)
        return day.weekday() < _SATURDAY and day not in self._trading_closed

    def is_settlement_day(self, day: date) -> bool:
        """Whether securities traded on this market settle on ``day``.

        Raises:
            CalendarCoverageError: ``day`` is outside the coverage window.
        """
        self._require_covered(day)
        return day.weekday() < _SATURDAY and day not in self._settlement_closed

    def add_settlement_days(self, start: date, business_days: int) -> date:
        """Return the date ``business_days`` settlement days after ``start``.

        ``start`` itself is not counted, so T+1 from a Friday is the next
        Monday when that Monday is a settlement day.

        Raises:
            ValueError: ``business_days`` is less than 1.
            CalendarCoverageError: The walk leaves the coverage window.
        """
        if business_days < 1:
            msg = f"business_days must be at least 1, got {business_days}"
            raise ValueError(msg)
        self._require_covered(start)
        day, remaining = start, business_days
        while remaining:
            day += timedelta(days=1)
            if self.is_settlement_day(day):
                remaining -= 1
        return day

    def settlement_day_on_or_after(self, day: date) -> date:
        """Return ``day`` if it is a settlement day, else the next one.

        Raises:
            CalendarCoverageError: The walk leaves the coverage window.
        """
        while not self.is_settlement_day(day):
            day += timedelta(days=1)
        return day

    def previous_settlement_day(self, day: date) -> date:
        """Return the last settlement day strictly before ``day``.

        Raises:
            CalendarCoverageError: The walk leaves the coverage window.
        """
        self._require_covered(day)
        day -= timedelta(days=1)
        while not self.is_settlement_day(day):
            day -= timedelta(days=1)
        return day

    def _require_covered(self, day: date) -> None:
        if not self.coverage.start <= day <= self.coverage.end:
            msg = (
                f"{day} is outside the {self.market} calendar coverage "
                f"{self.coverage.start}..{self.coverage.end} (version {self.version})"
            )
            raise CalendarCoverageError(msg)
