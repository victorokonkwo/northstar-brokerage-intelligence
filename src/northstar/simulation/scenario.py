"""Scenario-driven brokerage simulator (Sprint 1).

A scenario file states exactly what happens: one account, its deposits, and
orders with their fills. ``generate_events`` turns it into canonical events
and applies brokerage policy on the way: the per-execution commission (fee
schedule) and the settlement date (calendar and settlement rules). The output
depends only on the scenario, the seed, and the policy versions, so the same
inputs always give the same events and IDs.

Random, seeded generation at scale (FR-SIM-01, FR-SIM-06) is Phase 2.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from northstar.common.exceptions import NotATradingDayError, ScenarioError
from northstar.common.ids import EntityType, source_id
from northstar.common.money import Currency, Money
from northstar.domain.calendar import BUSINESS_TIMEZONE, MarketCalendar, business_date
from northstar.domain.events import (
    AccountCreated,
    AccountCreatedPayload,
    AccountType,
    CashDeposited,
    CashDepositedPayload,
    Event,
    OrderPlaced,
    OrderPlacedPayload,
    OrderType,
    PolicyVersions,
    PositiveAmount,
    Price,
    Quantity,
    Side,
    TradeExecuted,
    TradeExecutedPayload,
    TradeSettled,
    TradeSettledPayload,
    UtcTimestamp,
)
from northstar.domain.settlement import AssetClass, SettlementRules, settlement_date
from northstar.domain.trading import FeeSchedule, gross_amount


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ScenarioInstrument(_Frozen):
    """A synthetic instrument. Symbols are invented, not real tickers."""

    key: str
    symbol: str
    name: str
    market: str
    asset_class: AssetClass
    currency: Currency
    multiplier: Annotated[int, Field(ge=1)] = 1


class ScenarioAccount(_Frozen):
    """The client account the scenario opens."""

    account_type: AccountType
    base_currency: Currency
    opened_at: UtcTimestamp


class ScenarioDeposit(_Frozen):
    """A cash deposit into the account."""

    at: UtcTimestamp
    amount: PositiveAmount
    currency: Currency


class ScenarioFill(_Frozen):
    """One execution of an order."""

    at: UtcTimestamp
    quantity: Quantity
    price: Price


class ScenarioOrder(_Frozen):
    """An order and the fills it receives.

    Raises:
        pydantic.ValidationError: The fills exceed the order quantity, are
            out of time order, or precede the order.
    """

    at: UtcTimestamp
    instrument: str
    side: Side
    order_type: OrderType
    quantity: Quantity
    limit_price: Price | None = None
    fills: tuple[ScenarioFill, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _fills_are_consistent(self) -> Self:
        if sum((f.quantity for f in self.fills), Decimal(0)) > self.quantity:
            msg = "fills exceed the order quantity"
            raise ValueError(msg)
        times = [self.at, *(f.at for f in self.fills)]
        if times != sorted(times):
            msg = "fills must not precede the order and must be in time order"
            raise ValueError(msg)
        return self


class Scenario(_Frozen):
    """A complete, scripted simulation scenario. Its values are illustrative.

    ``settlement_time_local`` is the America/Toronto time at which
    ``trade_settled`` events are stamped on the settlement date.

    Raises:
        pydantic.ValidationError: Instrument keys repeat, or an order names
            an unknown instrument.
    """

    scenario_id: str
    description: str
    illustrative: bool
    settlement_time_local: time
    account: ScenarioAccount
    instruments: tuple[ScenarioInstrument, ...]
    deposits: tuple[ScenarioDeposit, ...]
    orders: tuple[ScenarioOrder, ...]

    @model_validator(mode="after")
    def _references_resolve(self) -> Self:
        keys = [i.key for i in self.instruments]
        if len(keys) != len(set(keys)):
            msg = "instrument keys must be unique"
            raise ValueError(msg)
        for order in self.orders:
            if order.instrument not in keys:
                msg = f"order references unknown instrument {order.instrument!r}"
                raise ValueError(msg)
        return self


@dataclass(frozen=True, slots=True)
class Policies:
    """The brokerage policy versions the simulator applies."""

    calendars: dict[str, MarketCalendar]
    settlement_rules: SettlementRules
    fee_schedule: FeeSchedule


type _Make = Callable[[str, datetime, str | None], Event]
"""Builds an event from its event ID, timestamp, and causation event ID."""


@dataclass(frozen=True, slots=True)
class _Draft:
    """An event that has everything except its event ID and causation ID."""

    timestamp: datetime
    ref: str
    caused_by: str | None
    make: _Make


class _Sequencer:
    """Hands out deterministic source IDs per entity type."""

    def __init__(self, seed: int) -> None:
        self._seed = seed
        self._last: dict[EntityType, int] = {}

    def next_id(self, entity: EntityType) -> str:
        sequence = self._last.get(entity, 0) + 1
        self._last[entity] = sequence
        return source_id(entity, self._seed, sequence)


def generate_events(scenario: Scenario, seed: int, policies: Policies) -> tuple[Event, ...]:
    """Return the scenario's canonical events in a deterministic order.

    Events are sorted by ``event_timestamp``. Ties keep generation order:
    account, deposits, then each order followed by its fills and settlements.
    Event IDs are numbered in the final order, and every execution names its
    order event, and every settlement its last execution event, as causation.

    Raises:
        ScenarioError: The scenario would need FX (a currency differs from
            the account's, OQ-01), a market has no calendar, or a settlement
            would be stamped before its last fill.
        NotATradingDayError: A fill is not on a trading day.
        PolicyNotFoundError: No commission or settlement cycle applies.
        CalendarCoverageError: A date is outside a calendar's coverage.
    """
    ids = _Sequencer(seed)
    account_id = ids.next_id(EntityType.ACCOUNT)
    base = scenario.account.base_currency
    instrument_ids = {i.key: ids.next_id(EntityType.INSTRUMENT) for i in scenario.instruments}
    instruments = {i.key: i for i in scenario.instruments}

    drafts = [_account_draft(scenario.account, account_id)]
    for deposit in scenario.deposits:
        _require_account_currency(deposit.currency, base, "deposit")
        drafts.append(_deposit_draft(deposit, account_id, ids.next_id(EntityType.CASH_MOVEMENT)))
    for order in scenario.orders:
        instrument = instruments[order.instrument]
        _require_account_currency(instrument.currency, base, f"instrument {instrument.key}")
        drafts.extend(
            _order_drafts(
                scenario,
                order,
                account_id,
                instrument_ids[order.instrument],
                instrument,
                ids,
                policies,
            )
        )

    ordered = sorted(drafts, key=lambda d: d.timestamp)  # stable: ties keep generation order
    event_ids: dict[str, str] = {}
    events: list[Event] = []
    for draft in ordered:
        event_id = ids.next_id(EntityType.EVENT)
        event_ids[draft.ref] = event_id
        causation = event_ids[draft.caused_by] if draft.caused_by is not None else None
        events.append(draft.make(event_id, draft.timestamp, causation))
    return tuple(events)


def _require_account_currency(currency: Currency, base: Currency, what: str) -> None:
    if currency is not base:
        msg = (
            f"{what} is in {currency} but the account is in {base}: FX conversion is not "
            "implemented yet (OQ-01)"
        )
        raise ScenarioError(msg)


def _account_draft(account: ScenarioAccount, account_id: str) -> _Draft:
    def make(event_id: str, ts: datetime, causation: str | None) -> Event:
        return AccountCreated(
            event_id=event_id,
            event_timestamp=ts,
            recorded_at=ts,
            correlation_id=account_id,
            causation_id=causation,
            payload=AccountCreatedPayload(
                account_id=account_id,
                account_type=account.account_type,
                base_currency=account.base_currency,
            ),
        )

    return _Draft(account.opened_at, account_id, None, make)


def _deposit_draft(deposit: ScenarioDeposit, account_id: str, cash_movement_id: str) -> _Draft:
    def make(event_id: str, ts: datetime, causation: str | None) -> Event:
        return CashDeposited(
            event_id=event_id,
            event_timestamp=ts,
            recorded_at=ts,
            correlation_id=account_id,
            causation_id=causation,
            payload=CashDepositedPayload(
                cash_movement_id=cash_movement_id,
                account_id=account_id,
                amount=deposit.amount,
                currency=deposit.currency,
            ),
        )

    return _Draft(deposit.at, cash_movement_id, None, make)


@dataclass(frozen=True, slots=True)
class _Fill:
    execution_id: str
    fill: ScenarioFill
    gross: Money


def _order_drafts(
    scenario: Scenario,
    order: ScenarioOrder,
    account_id: str,
    instrument_id: str,
    instrument: ScenarioInstrument,
    ids: _Sequencer,
    policies: Policies,
) -> list[_Draft]:
    calendar = policies.calendars.get(instrument.market)
    if calendar is None:
        msg = f"no calendar for market {instrument.market}"
        raise ScenarioError(msg)
    versions = PolicyVersions(
        calendar=calendar.version,
        settlement_rules=policies.settlement_rules.version,
        fee_schedule=policies.fee_schedule.version,
    )
    order_id = ids.next_id(EntityType.ORDER)

    def make_order(event_id: str, ts: datetime, causation: str | None) -> Event:
        return OrderPlaced(
            event_id=event_id,
            event_timestamp=ts,
            recorded_at=ts,
            correlation_id=order_id,
            causation_id=causation,
            payload=OrderPlacedPayload(
                order_id=order_id,
                account_id=account_id,
                instrument_id=instrument_id,
                market=instrument.market,
                side=order.side,
                order_type=order.order_type,
                quantity=order.quantity,
                limit_price=order.limit_price,
                currency=instrument.currency,
            ),
        )

    drafts = [_Draft(order.at, order_id, None, make_order)]
    obligations: dict[date, tuple[str, date, list[_Fill]]] = {}
    for fill in order.fills:
        trade_date = business_date(fill.at)
        if not calendar.is_trading_day(trade_date):
            msg = (
                f"fill at {fill.at.isoformat()} is on {trade_date}, "
                f"not a {instrument.market} trading day"
            )
            raise NotATradingDayError(msg)
        if trade_date not in obligations:
            settles_on = settlement_date(
                trade_date,
                instrument.market,
                instrument.asset_class,
                policies.settlement_rules,
                calendar,
            )
            obligations[trade_date] = (ids.next_id(EntityType.SETTLEMENT), settles_on, [])
        settlement_id, settles_on, fills = obligations[trade_date]
        execution_id = ids.next_id(EntityType.EXECUTION)
        commission = policies.fee_schedule.commission(
            instrument.market, instrument.currency, trade_date, fill.quantity
        )
        clearing_fee = policies.fee_schedule.clearing_fee(
            instrument.market, instrument.currency, trade_date, fill.quantity
        )
        payload = TradeExecutedPayload(
            execution_id=execution_id,
            order_id=order_id,
            account_id=account_id,
            instrument_id=instrument_id,
            market=instrument.market,
            asset_class=instrument.asset_class,
            side=order.side,
            quantity=fill.quantity,
            price=fill.price,
            multiplier=instrument.multiplier,
            currency=instrument.currency,
            commission=commission.amount,
            clearing_fee=clearing_fee.amount,
            trade_date=trade_date,
            settlement_date=settles_on,
            settlement_id=settlement_id,
            policy_versions=versions,
        )
        fills.append(
            _Fill(
                execution_id,
                fill,
                gross_amount(fill.quantity, fill.price, instrument.multiplier, instrument.currency),
            )
        )
        drafts.append(_Draft(fill.at, execution_id, order_id, _execution_maker(order_id, payload)))

    for trade_date, (settlement_id, settles_on, fills) in obligations.items():
        stamped = datetime.combine(
            settles_on, scenario.settlement_time_local, tzinfo=BUSINESS_TIMEZONE
        ).astimezone(UTC)
        last_fill = fills[-1]
        if stamped <= last_fill.fill.at:
            msg = f"settlement {settlement_id} would be stamped before its last fill"
            raise ScenarioError(msg)
        settled = TradeSettledPayload(
            settlement_id=settlement_id,
            order_id=order_id,
            account_id=account_id,
            instrument_id=instrument_id,
            side=order.side,
            trade_date=trade_date,
            settlement_date=settles_on,
            quantity=sum((f.fill.quantity for f in fills), Decimal(0)),
            amount=sum((f.gross for f in fills), Money.zero(instrument.currency)).amount,
            currency=instrument.currency,
            execution_ids=tuple(f.execution_id for f in fills),
        )
        drafts.append(
            _Draft(
                stamped, settlement_id, last_fill.execution_id, _settlement_maker(order_id, settled)
            )
        )
    return drafts


def _execution_maker(order_id: str, payload: TradeExecutedPayload) -> _Make:
    def make(event_id: str, ts: datetime, causation: str | None) -> Event:
        return TradeExecuted(
            event_id=event_id,
            event_timestamp=ts,
            recorded_at=ts,
            correlation_id=order_id,
            causation_id=causation,
            payload=payload,
        )

    return make


def _settlement_maker(order_id: str, payload: TradeSettledPayload) -> _Make:
    def make(event_id: str, ts: datetime, causation: str | None) -> Event:
        return TradeSettled(
            event_id=event_id,
            event_timestamp=ts,
            recorded_at=ts,
            correlation_id=order_id,
            causation_id=causation,
            payload=payload,
        )

    return make
