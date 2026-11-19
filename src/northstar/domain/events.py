"""Canonical brokerage events: envelope and payloads (domain model §4.2).

These models are the contract between the simulator, the ledger, and the
warehouse. The JSON Schemas in ``contracts/events/`` are generated from them.

Rules enforced on every event:

- Timestamps are timezone-aware **UTC**. Naive or non-UTC values are rejected,
  so serialized events have one canonical form.
- Amounts, prices, and quantities are ``Decimal``. A ``float`` is rejected,
  never converted (ADR 004). In JSON they are fixed-point decimal strings.
- Unknown fields are rejected, and every model is immutable.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    model_validator,
)

from northstar.common.money import Currency
from northstar.domain.settlement import AssetClass

SCHEMA_VERSION: Final = 1
"""Payload schema version of every event type defined here."""


def _reject_float(value: object) -> object:
    if isinstance(value, float):
        # ValueError, not TypeError, so pydantic reports it as a ValidationError.
        msg = "floats are not accepted for amounts, prices, or quantities; use a decimal string"
        raise ValueError(msg)  # noqa: TRY004
    return value


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        msg = f"timestamps must be timezone-aware UTC, got {value.isoformat()}"
        raise ValueError(msg)
    return value


def _fixed_point(value: Decimal) -> str:
    return format(value, "f")


PositiveAmount = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(max_digits=18, decimal_places=2, gt=0),
    PlainSerializer(_fixed_point, return_type=str, when_used="json"),
]
"""A strictly positive monetary amount in minor units (numeric(18, 2))."""

NonNegativeAmount = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(max_digits=18, decimal_places=2, ge=0),
    PlainSerializer(_fixed_point, return_type=str, when_used="json"),
]
"""A monetary amount that may be zero, such as a waived commission."""

Price = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(max_digits=18, decimal_places=8, gt=0),
    PlainSerializer(_fixed_point, return_type=str, when_used="json"),
]
"""A strictly positive per-unit price (numeric(18, 8))."""

Quantity = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(max_digits=18, decimal_places=8, gt=0),
    PlainSerializer(_fixed_point, return_type=str, when_used="json"),
]
"""A strictly positive quantity; fractional shares allowed (numeric(18, 8))."""

UtcTimestamp = Annotated[datetime, AfterValidator(_require_utc)]
"""A timezone-aware UTC timestamp."""


class EventType(StrEnum):
    """Canonical event types implemented so far."""

    ACCOUNT_CREATED = "account_created"
    CASH_DEPOSITED = "cash_deposited"
    ORDER_PLACED = "order_placed"
    TRADE_EXECUTED = "trade_executed"
    TRADE_SETTLED = "trade_settled"


class AccountType(StrEnum):
    """Client account types (domain model §1)."""

    CASH = "CASH"
    MARGIN = "MARGIN"


class Side(StrEnum):
    """Order and trade direction. Long only: a SELL reduces an existing holding."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    """Order types."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PolicyVersions(_Frozen):
    """Versions of the policies a source system applied to produce an event (W2)."""

    calendar: str
    settlement_rules: str
    fee_schedule: str


class AccountCreatedPayload(_Frozen):
    """A client account was opened."""

    account_id: str
    account_type: AccountType
    base_currency: Currency


class CashDepositedPayload(_Frozen):
    """Client cash arrived in the account's currency."""

    cash_movement_id: str
    account_id: str
    amount: PositiveAmount
    currency: Currency


class OrderPlacedPayload(_Frozen):
    """A client order was accepted.

    Raises:
        pydantic.ValidationError: A LIMIT order has no limit price, or a
            MARKET order has one.
    """

    order_id: str
    account_id: str
    instrument_id: str
    market: str
    side: Side
    order_type: OrderType
    quantity: Quantity
    limit_price: Price | None = None
    currency: Currency

    @model_validator(mode="after")
    def _limit_price_matches_order_type(self) -> Self:
        if (self.order_type is OrderType.LIMIT) != (self.limit_price is not None):
            msg = "limit_price is required for LIMIT orders and forbidden for MARKET orders"
            raise ValueError(msg)
        return self


class TradeExecutedPayload(_Frozen):
    """One fill of an order, with the commission charged for it (per execution, OQ-02)."""

    execution_id: str
    order_id: str
    account_id: str
    instrument_id: str
    market: str
    asset_class: AssetClass
    side: Side
    quantity: Quantity
    price: Price
    multiplier: Annotated[int, Field(ge=1)]
    currency: Currency
    commission: NonNegativeAmount
    trade_date: date
    settlement_date: date
    settlement_id: str
    policy_versions: PolicyVersions


class TradeSettledPayload(_Frozen):
    """A settlement obligation settled: one order's executions on one trade date.

    ``amount`` is the sum of the executions' gross amounts, each rounded to the
    currency's minor unit at posting (INV-06).
    """

    settlement_id: str
    order_id: str
    account_id: str
    instrument_id: str
    side: Side
    trade_date: date
    settlement_date: date
    quantity: Quantity
    amount: PositiveAmount
    currency: Currency
    execution_ids: tuple[str, ...] = Field(min_length=1)


class _Envelope(_Frozen):
    event_id: str
    schema_version: Literal[1] = SCHEMA_VERSION
    source_system: Literal["brokerage_simulator"] = "brokerage_simulator"
    event_timestamp: UtcTimestamp
    recorded_at: UtcTimestamp
    correlation_id: str
    causation_id: str | None = None

    @model_validator(mode="after")
    def _recorded_after_event(self) -> Self:
        if self.recorded_at < self.event_timestamp:
            msg = "recorded_at must not be earlier than event_timestamp"
            raise ValueError(msg)
        return self


class AccountCreated(_Envelope):
    """``account_created`` event."""

    event_type: Literal[EventType.ACCOUNT_CREATED] = EventType.ACCOUNT_CREATED
    payload: AccountCreatedPayload


class CashDeposited(_Envelope):
    """``cash_deposited`` event."""

    event_type: Literal[EventType.CASH_DEPOSITED] = EventType.CASH_DEPOSITED
    payload: CashDepositedPayload


class OrderPlaced(_Envelope):
    """``order_placed`` event."""

    event_type: Literal[EventType.ORDER_PLACED] = EventType.ORDER_PLACED
    payload: OrderPlacedPayload


class TradeExecuted(_Envelope):
    """``trade_executed`` event."""

    event_type: Literal[EventType.TRADE_EXECUTED] = EventType.TRADE_EXECUTED
    payload: TradeExecutedPayload


class TradeSettled(_Envelope):
    """``trade_settled`` event."""

    event_type: Literal[EventType.TRADE_SETTLED] = EventType.TRADE_SETTLED
    payload: TradeSettledPayload


Event = Annotated[
    AccountCreated | CashDeposited | OrderPlaced | TradeExecuted | TradeSettled,
    Field(discriminator="event_type"),
]
"""Any canonical event, discriminated by ``event_type``."""

EVENT_MODELS: dict[EventType, type[_Envelope]] = {
    EventType.ACCOUNT_CREATED: AccountCreated,
    EventType.CASH_DEPOSITED: CashDeposited,
    EventType.ORDER_PLACED: OrderPlaced,
    EventType.TRADE_EXECUTED: TradeExecuted,
    EventType.TRADE_SETTLED: TradeSettled,
}
"""Event model per type, used to export the JSON Schemas."""
