"""Contractual settlement dates (domain model §5).

The settlement date is the trade date plus N settlement days on the market's
calendar. N comes from effective-dated policy (``SettlementRules``), never
from a constant, so a change of cycle (such as T+2 to T+1) is a new policy
entry rather than a code change.
"""

from collections.abc import Iterable
from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from northstar.common.exceptions import NotATradingDayError, PolicyNotFoundError
from northstar.domain.calendar import MarketCalendar


class AssetClass(StrEnum):
    """Asset classes with their own settlement cycles."""

    EQUITY = "EQUITY"
    OPTION = "OPTION"


class SettlementCycle(BaseModel):
    """Settlement lag for a market and asset classes from a trade date onward."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    asset_classes: tuple[AssetClass, ...] = Field(min_length=1)
    business_days: int = Field(ge=1)
    effective_from: date
    source: str


class SettlementRules(BaseModel):
    """Versioned set of settlement cycles.

    Raises:
        pydantic.ValidationError: On construction, if two cycles start on the
            same date for the same market and asset class.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    effective_from: date
    cycles: tuple[SettlementCycle, ...]

    @model_validator(mode="after")
    def _no_conflicting_cycles(self) -> Self:
        seen: set[tuple[str, AssetClass, date]] = set()
        for cycle in self.cycles:
            for asset_class in cycle.asset_classes:
                key = (cycle.market, asset_class, cycle.effective_from)
                if key in seen:
                    msg = f"two cycles start on {key[2]} for {key[0]} {key[1]}"
                    raise ValueError(msg)
                seen.add(key)
        return self

    def cycle_for(self, market: str, asset_class: AssetClass, trade_date: date) -> SettlementCycle:
        """Return the cycle in force on ``trade_date``: the latest one started by then.

        Raises:
            PolicyNotFoundError: No cycle for this market and asset class
                starts on or before ``trade_date``.
        """
        candidates: Iterable[SettlementCycle] = (
            c
            for c in self.cycles
            if c.market == market
            and asset_class in c.asset_classes
            and c.effective_from <= trade_date
        )
        cycle = max(candidates, key=lambda c: c.effective_from, default=None)
        if cycle is None:
            msg = (
                f"no settlement cycle for {market} {asset_class} on trade date {trade_date} "
                f"in settlement rules version {self.version}"
            )
            raise PolicyNotFoundError(msg)
        return cycle


def settlement_date(
    trade_date: date,
    market: str,
    asset_class: AssetClass,
    rules: SettlementRules,
    calendar: MarketCalendar,
) -> date:
    """Return the contractual settlement date of a trade.

    Raises:
        ValueError: ``calendar`` is for a different market.
        NotATradingDayError: ``trade_date`` is not a trading day.
        PolicyNotFoundError: No settlement cycle applies on ``trade_date``.
        CalendarCoverageError: A date needed is outside the calendar's coverage.
    """
    if calendar.market != market:
        msg = f"calendar is for {calendar.market}, trade is on {market}"
        raise ValueError(msg)
    if not calendar.is_trading_day(trade_date):
        msg = f"{trade_date} is not a {market} trading day"
        raise NotATradingDayError(msg)
    cycle = rules.cycle_for(market, asset_class, trade_date)
    return calendar.add_settlement_days(trade_date, cycle.business_days)
