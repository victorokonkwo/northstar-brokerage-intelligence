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
"""Payload schema version of every event type that has not changed since v1.

``trade_executed`` is at version 2 (an additive, optional ``clearing_fee``).
Consumers accept the versions each model lists and reject any other.
"""


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

FxRate = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(max_digits=18, decimal_places=10, gt=0),
    PlainSerializer(_fixed_point, return_type=str, when_used="json"),
]
"""A strictly positive rate with up to 10 decimal places (numeric(18, 10), ADR 004)."""

FeeRate = Annotated[
    Decimal,
    BeforeValidator(_reject_float),
    Field(max_digits=18, decimal_places=8, ge=0),
    PlainSerializer(_fixed_point, return_type=str, when_used="json"),
]
"""A non-negative per-unit fee rate (numeric(18, 8))."""

UtcTimestamp = Annotated[datetime, AfterValidator(_require_utc)]
"""A timezone-aware UTC timestamp."""


class EventType(StrEnum):
    """Canonical event types implemented so far."""

    ACCOUNT_CREATED = "account_created"
    INSTRUMENT_LISTED = "instrument_listed"
    CASH_DEPOSITED = "cash_deposited"
    CASH_WITHDRAWN = "cash_withdrawn"
    FX_CONVERTED = "fx_converted"
    ORDER_PLACED = "order_placed"
    ORDER_CANCELLED = "order_cancelled"
    TRADE_EXECUTED = "trade_executed"
    TRADE_SETTLED = "trade_settled"
    PRICE_UPDATED = "price_updated"
    MARGIN_INTEREST_ACCRUED = "margin_interest_accrued"


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


class OptionRight(StrEnum):
    """Call or put."""

    CALL = "CALL"
    PUT = "PUT"


class ExerciseStyle(StrEnum):
    """When an option may be exercised (exercise itself is Phase 5, OQ-03)."""

    AMERICAN = "AMERICAN"
    EUROPEAN = "EUROPEAN"


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
    clearing_fee: NonNegativeAmount = Decimal("0.00")
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


class OptionTerms(_Frozen):
    """Contract terms of a listed equity option."""

    underlying_instrument_id: str
    strike: Price
    expiry: date
    right: OptionRight
    exercise_style: ExerciseStyle


class InstrumentListedPayload(_Frozen):
    """Reference data for a synthetic instrument (symbols are invented).

    Raises:
        pydantic.ValidationError: Option terms are missing for an option or
            present for anything else.
    """

    instrument_id: str
    symbol: str
    name: str
    market: str
    asset_class: AssetClass
    currency: Currency
    multiplier: Annotated[int, Field(ge=1)]
    option: OptionTerms | None = None

    @model_validator(mode="after")
    def _option_terms_match_asset_class(self) -> Self:
        if (self.asset_class is AssetClass.OPTION) != (self.option is not None):
            msg = "option terms are required for options and forbidden otherwise"
            raise ValueError(msg)
        return self


class CashWithdrawnPayload(_Frozen):
    """Client cash left the account."""

    cash_movement_id: str
    account_id: str
    amount: PositiveAmount
    currency: Currency


class FxConvertedPayload(_Frozen):
    """A client conversion between CAD and USD at the client rate.

    Rates are CAD per 1 USD. ``client_rate`` includes the spread, and
    ``reference_rate`` is the rate the spread is measured against (OQ-01).

    Raises:
        pydantic.ValidationError: The currencies are equal or not CAD and USD.
    """

    fx_conversion_id: str
    account_id: str
    sell_amount: PositiveAmount
    sell_currency: Currency
    buy_amount: PositiveAmount
    buy_currency: Currency
    reference_rate: FxRate
    client_rate: FxRate
    rate_date: date
    rate_source: Literal["synthetic"]
    fx_policy_version: str

    @model_validator(mode="after")
    def _cad_usd_pair(self) -> Self:
        if {self.sell_currency, self.buy_currency} != {Currency.CAD, Currency.USD}:
            msg = "an FX conversion is between CAD and USD"
            raise ValueError(msg)
        return self


class OrderCancelledPayload(_Frozen):
    """The unfilled remainder of an order was cancelled. Fills stand."""

    order_id: str
    account_id: str
    cancelled_quantity: Quantity


class PriceUpdatedPayload(_Frozen):
    """A daily closing price for an instrument (synthetic in Phase 2)."""

    instrument_id: str
    price_date: date
    close_price: Price
    currency: Currency
    source: Literal["synthetic"]


class MarginInterestAccruedPayload(_Frozen):
    """One day of interest on a client's debit (margin loan) balance in one currency."""

    account_id: str
    accrual_date: date
    currency: Currency
    debit_balance: PositiveAmount
    annual_rate: FxRate
    day_count: Literal["ACT/365"]
    amount: PositiveAmount
    margin_policy_version: str


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
    """``trade_executed`` event, schema version 2 (v1 events stay valid).

    Raises:
        pydantic.ValidationError: A version 1 event carries a clearing fee,
            which v1 did not have.
    """

    schema_version: Literal[1, 2] = 2  # type: ignore[assignment]
    event_type: Literal[EventType.TRADE_EXECUTED] = EventType.TRADE_EXECUTED
    payload: TradeExecutedPayload

    @model_validator(mode="after")
    def _v1_has_no_clearing_fee(self) -> Self:
        if self.schema_version == 1 and self.payload.clearing_fee != 0:
            msg = "trade_executed v1 has no clearing_fee; use schema_version 2"
            raise ValueError(msg)
        return self


class TradeSettled(_Envelope):
    """``trade_settled`` event."""

    event_type: Literal[EventType.TRADE_SETTLED] = EventType.TRADE_SETTLED
    payload: TradeSettledPayload


class InstrumentListed(_Envelope):
    """``instrument_listed`` event."""

    event_type: Literal[EventType.INSTRUMENT_LISTED] = EventType.INSTRUMENT_LISTED
    payload: InstrumentListedPayload


class CashWithdrawn(_Envelope):
    """``cash_withdrawn`` event."""

    event_type: Literal[EventType.CASH_WITHDRAWN] = EventType.CASH_WITHDRAWN
    payload: CashWithdrawnPayload


class FxConverted(_Envelope):
    """``fx_converted`` event."""

    event_type: Literal[EventType.FX_CONVERTED] = EventType.FX_CONVERTED
    payload: FxConvertedPayload


class OrderCancelled(_Envelope):
    """``order_cancelled`` event."""

    event_type: Literal[EventType.ORDER_CANCELLED] = EventType.ORDER_CANCELLED
    payload: OrderCancelledPayload


class PriceUpdated(_Envelope):
    """``price_updated`` event."""

    event_type: Literal[EventType.PRICE_UPDATED] = EventType.PRICE_UPDATED
    payload: PriceUpdatedPayload


class MarginInterestAccrued(_Envelope):
    """``margin_interest_accrued`` event."""

    event_type: Literal[EventType.MARGIN_INTEREST_ACCRUED] = EventType.MARGIN_INTEREST_ACCRUED
    payload: MarginInterestAccruedPayload


Event = Annotated[
    AccountCreated
    | InstrumentListed
    | CashDeposited
    | CashWithdrawn
    | FxConverted
    | OrderPlaced
    | OrderCancelled
    | TradeExecuted
    | TradeSettled
    | PriceUpdated
    | MarginInterestAccrued,
    Field(discriminator="event_type"),
]
"""Any canonical event, discriminated by ``event_type``."""

EVENT_MODELS: dict[EventType, type[_Envelope]] = {
    EventType.ACCOUNT_CREATED: AccountCreated,
    EventType.INSTRUMENT_LISTED: InstrumentListed,
    EventType.CASH_DEPOSITED: CashDeposited,
    EventType.CASH_WITHDRAWN: CashWithdrawn,
    EventType.FX_CONVERTED: FxConverted,
    EventType.ORDER_PLACED: OrderPlaced,
    EventType.ORDER_CANCELLED: OrderCancelled,
    EventType.TRADE_EXECUTED: TradeExecuted,
    EventType.TRADE_SETTLED: TradeSettled,
    EventType.PRICE_UPDATED: PriceUpdated,
    EventType.MARGIN_INTEREST_ACCRUED: MarginInterestAccrued,
}
"""Event model per type, used to export the JSON Schemas."""
