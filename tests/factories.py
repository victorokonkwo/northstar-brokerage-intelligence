"""Shared test builders and Hypothesis strategies (importable via pytest ``pythonpath``)."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from functools import cache
from pathlib import Path

from hypothesis import strategies as st

from northstar.common.money import Currency
from northstar.domain.accounting import ChartOfAccounts, PostingEngine
from northstar.domain.calendar import BUSINESS_TIMEZONE, MarketCalendar
from northstar.domain.events import (
    CashDeposited,
    CashDepositedPayload,
    PolicyVersions,
    Side,
    TradeExecuted,
    TradeExecutedPayload,
    TradeSettled,
    TradeSettledPayload,
)
from northstar.domain.settlement import AssetClass, SettlementRules
from northstar.domain.trading import FeeSchedule
from northstar.infrastructure.config_loader import load_policy
from northstar.simulation.scenario import Policies

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / "config"


@cache
def xtse() -> MarketCalendar:
    return load_policy(CONFIG / "calendars" / "xtse.yaml", MarketCalendar)


@cache
def chart() -> ChartOfAccounts:
    return load_policy(CONFIG / "accounting" / "chart_of_accounts.yaml", ChartOfAccounts)


@cache
def policies() -> Policies:
    return Policies(
        calendars={"XTSE": xtse()},
        settlement_rules=load_policy(
            CONFIG / "brokerage" / "settlement_rules.yaml", SettlementRules
        ),
        fee_schedule=load_policy(CONFIG / "brokerage" / "fee_schedule.yaml", FeeSchedule),
    )


@cache
def engine() -> PostingEngine:
    return PostingEngine(chart(), xtse())


VERSIONS = PolicyVersions(calendar="test", settlement_rules="test", fee_schedule="test")


def at_toronto(day: date, hour: int = 10, minute: int = 0) -> datetime:
    """A UTC timestamp at the given Toronto wall-clock time on ``day``."""
    return datetime.combine(day, time(hour, minute), tzinfo=BUSINESS_TIMEZONE).astimezone(UTC)


def deposit(
    amount: str = "100.00",
    currency: Currency = Currency.CAD,
    account_id: str = "acc_s1_000001",
    when: datetime | None = None,
    event_id: str = "evt_s1_000001",
) -> CashDeposited:
    ts = when or at_toronto(date(2026, 3, 3))
    return CashDeposited(
        event_id=event_id,
        event_timestamp=ts,
        recorded_at=ts,
        correlation_id=account_id,
        payload=CashDepositedPayload(
            cash_movement_id="cm_s1_000001",
            account_id=account_id,
            amount=Decimal(amount),
            currency=currency,
        ),
    )


def execution(
    side: Side = Side.BUY,
    quantity: str = "10",
    price: str = "250.00",
    commission: str = "1.00",
    currency: Currency = Currency.USD,
    account_id: str = "acc_s1_000001",
    when: datetime | None = None,
    event_id: str = "evt_s1_000002",
    execution_id: str = "exe_s1_000001",
) -> TradeExecuted:
    ts = when or at_toronto(date(2026, 3, 3))
    trade_date = ts.astimezone(BUSINESS_TIMEZONE).date()
    return TradeExecuted(
        event_id=event_id,
        event_timestamp=ts,
        recorded_at=ts,
        correlation_id="ord_s1_000001",
        payload=TradeExecutedPayload(
            execution_id=execution_id,
            order_id="ord_s1_000001",
            account_id=account_id,
            instrument_id="ins_s1_000001",
            market="XTSE",
            asset_class=AssetClass.EQUITY,
            side=side,
            quantity=Decimal(quantity),
            price=Decimal(price),
            multiplier=1,
            currency=currency,
            commission=Decimal(commission),
            trade_date=trade_date,
            settlement_date=trade_date + timedelta(days=1),
            settlement_id="stl_s1_000001",
            policy_versions=VERSIONS,
        ),
    )


def settlement(
    side: Side = Side.BUY,
    amount: str = "2500.00",
    currency: Currency = Currency.USD,
    when: datetime | None = None,
    event_id: str = "evt_s1_000003",
) -> TradeSettled:
    ts = when or at_toronto(date(2026, 3, 4))
    return TradeSettled(
        event_id=event_id,
        event_timestamp=ts,
        recorded_at=ts,
        correlation_id="ord_s1_000001",
        payload=TradeSettledPayload(
            settlement_id="stl_s1_000001",
            order_id="ord_s1_000001",
            account_id="acc_s1_000001",
            instrument_id="ins_s1_000001",
            side=side,
            trade_date=date(2026, 3, 3),
            settlement_date=date(2026, 3, 4),
            quantity=Decimal(10),
            amount=Decimal(amount),
            currency=currency,
            execution_ids=("exe_s1_000001",),
        ),
    )


# --- Hypothesis strategies -------------------------------------------------

currencies = st.sampled_from(Currency)
sides = st.sampled_from(Side)

# Amounts that fit numeric(18, 2) with room for sums: up to 10^12.
positive_amounts = st.integers(min_value=1, max_value=10**14).map(
    lambda cents: str(Decimal(cents).scaleb(-2))
)
commissions = st.integers(min_value=0, max_value=10**4).map(
    lambda cents: str(Decimal(cents).scaleb(-2))
)
quantities = st.decimals(min_value=Decimal("0.00000001"), max_value=Decimal(10**6), places=8).map(
    lambda d: format(d.normalize(), "f")
)
prices = st.decimals(min_value=Decimal("0.0001"), max_value=Decimal(10**5), places=8).map(
    lambda d: format(d.normalize(), "f")
)


@st.composite
def covered_timestamps(draw: st.DrawFn) -> datetime:
    """A UTC timestamp whose business date and posting date are inside XTSE coverage."""
    day = draw(st.dates(min_value=date(2026, 1, 1), max_value=date(2026, 12, 23)))
    return at_toronto(day, draw(st.integers(0, 23)), draw(st.integers(0, 59)))
