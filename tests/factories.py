"""Shared test builders and Hypothesis strategies (importable via pytest ``pythonpath``)."""

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from functools import cache
from pathlib import Path

from hypothesis import strategies as st

from northstar.application.policies import load_policies
from northstar.common.money import Currency
from northstar.domain.accounting import ChartOfAccounts, PostingEngine
from northstar.domain.calendar import BUSINESS_TIMEZONE, MarketCalendar
from northstar.domain.events import (
    CashDeposited,
    CashDepositedPayload,
    CashWithdrawn,
    CashWithdrawnPayload,
    Event,
    FxConverted,
    FxConvertedPayload,
    MarginInterestAccrued,
    MarginInterestAccruedPayload,
    PolicyVersions,
    Side,
    TradeExecuted,
    TradeExecutedPayload,
    TradeSettled,
    TradeSettledPayload,
)
from northstar.domain.settlement import AssetClass
from northstar.infrastructure.config_loader import load_policy
from northstar.simulation.engine import Policies

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
    return load_policies(CONFIG)


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
    clearing_fee: str = "0.00",
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
            clearing_fee=Decimal(clearing_fee),
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


def withdrawal(
    amount: str = "100.00",
    currency: Currency = Currency.CAD,
    account_id: str = "acc_s1_000001",
    when: datetime | None = None,
    event_id: str = "evt_s1_000004",
) -> CashWithdrawn:
    ts = when or at_toronto(date(2026, 3, 5))
    return CashWithdrawn(
        event_id=event_id,
        event_timestamp=ts,
        recorded_at=ts,
        correlation_id=account_id,
        payload=CashWithdrawnPayload(
            cash_movement_id="cm_s1_000002",
            account_id=account_id,
            amount=Decimal(amount),
            currency=currency,
        ),
    )


def fx_conversion(
    sell_amount: str,
    sell_currency: Currency,
    buy_amount: str,
    reference_rate: str = "1.3700000000",
    client_rate: str = "1.3905500000",
    account_id: str = "acc_s1_000001",
    when: datetime | None = None,
    event_id: str = "evt_s1_000005",
) -> FxConverted:
    ts = when or at_toronto(date(2026, 3, 3))
    buy_currency = Currency.USD if sell_currency is Currency.CAD else Currency.CAD
    return FxConverted(
        event_id=event_id,
        event_timestamp=ts,
        recorded_at=ts,
        correlation_id=account_id,
        payload=FxConvertedPayload(
            fx_conversion_id="fxc_s1_000001",
            account_id=account_id,
            sell_amount=Decimal(sell_amount),
            sell_currency=sell_currency,
            buy_amount=Decimal(buy_amount),
            buy_currency=buy_currency,
            reference_rate=Decimal(reference_rate),
            client_rate=Decimal(client_rate),
            rate_date=ts.astimezone(BUSINESS_TIMEZONE).date(),
            rate_source="synthetic",
            fx_policy_version="test",
        ),
    )


def interest(
    amount: str = "1.64",
    debit_balance: str = "10000.00",
    currency: Currency = Currency.CAD,
    account_id: str = "acc_s1_000001",
    accrual_date: date = date(2026, 3, 3),
    event_id: str = "evt_s1_000006",
) -> MarginInterestAccrued:
    ts = at_toronto(accrual_date, 23, 59)
    return MarginInterestAccrued(
        event_id=event_id,
        event_timestamp=ts,
        recorded_at=ts,
        correlation_id=account_id,
        payload=MarginInterestAccruedPayload(
            account_id=account_id,
            accrual_date=accrual_date,
            currency=currency,
            debit_balance=Decimal(debit_balance),
            annual_rate=Decimal("0.06"),
            day_count="ACT/365",
            amount=Decimal(amount),
            margin_policy_version="test",
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


CENT = Decimal("0.01")


def expected_client_cash(events: Iterable[Event]) -> dict[tuple[str, Currency], Decimal]:
    """Client cash per (account, currency), recomputed from the events alone."""
    balance: dict[tuple[str, Currency], Decimal] = defaultdict(Decimal)
    for event in events:
        match event:
            case CashDeposited(payload=p):
                balance[(p.account_id, p.currency)] += p.amount
            case CashWithdrawn(payload=p):
                balance[(p.account_id, p.currency)] -= p.amount
            case FxConverted(payload=p):
                balance[(p.account_id, p.sell_currency)] -= p.sell_amount
                balance[(p.account_id, p.buy_currency)] += p.buy_amount
            case MarginInterestAccrued(payload=p):
                balance[(p.account_id, p.currency)] -= p.amount
            case TradeExecuted(payload=p):
                gross = (p.quantity * p.price * p.multiplier).quantize(
                    CENT, rounding=ROUND_HALF_EVEN
                )
                if p.side is Side.BUY:
                    balance[(p.account_id, p.currency)] -= gross + p.commission
                else:
                    balance[(p.account_id, p.currency)] += gross - p.commission
            case TradeSettled():
                pass  # settlement moves firm cash and clearing balances, never client cash
    return dict(balance)
