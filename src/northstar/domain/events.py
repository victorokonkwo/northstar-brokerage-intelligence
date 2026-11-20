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
    ACCOUNT_UPDATED = "account_updated"
    FX_RATE_PUBLISHED = "fx_rate_published"
    DIVIDEND_DECLARED = "dividend_declared"
    DIVIDEND_PAID = "dividend_paid"


class AccountType(StrEnum):
    """Client account types (domain model §1)."""

    CASH = "CASH"
    MARGIN = "MARGIN"


class AccountStatus(StrEnum):
    """Whether an account may trade. A new account is ``ACTIVE``."""

    ACTIVE = "ACTIVE"
    TRADING_RESTRICTED = "TRADING_RESTRICTED"
    """No new orders. Cash movements, settlement, and interest continue."""


class AccountUpdateReason(StrEnum):
    """Why an account's attributes changed."""

    MARGIN_APPROVED = "MARGIN_APPROVED"
    TRADING_RESTRICTED = "TRADING_RESTRICTED"
    RESTRICTION_LIFTED = "RESTRICTION_LIFTED"


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


class AccountUpdatedPayload(_Frozen):
    """An account's attributes changed. The payload carries the full new state.

    Each update starts a new version of the account in ``dim_account`` (SCD2,
    FR-M1-05), valid from the event timestamp.
    """

    account_id: str
    account_type: AccountType
    status: AccountStatus
    reason: AccountUpdateReason


class FxRatePublishedPayload(_Frozen):
    """The day's USD/CAD reference rate (CAD per 1 USD), as published by its source.

    It is the rate client conversions are priced against (OQ-01) and the rate
    the warehouse translates USD amounts to CAD with (INV-07). The source is
    the simulator's synthetic path, not Bank of Canada data.

    Raises:
        pydantic.ValidationError: The pair is not USD/CAD.
    """

    base_currency: Literal[Currency.USD]
    quote_currency: Literal[Currency.CAD]
    rate_date: date
    rate: FxRate
    rate_source: Literal["synthetic"]
    fx_policy_version: str


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


_CENT: Final = Decimal("0.01")


class DividendDeclaredPayload(_Frozen):
    """An issuer's cash dividend declaration (roadmap 5C-a, ADR 013).

    Under T+1 the ex-dividend date is the record date (FINRA Rule 11140; TSX
    Ops Notice 2024-015), so both are carried and must be equal. The rate is
    per share in whole cents, so every allocation is exact. That the record
    date is a business day is checked against the calendar by the brokerage.

    Raises:
        pydantic.ValidationError: The dates are out of order, the ex-date isn't
            the record date, or the rate isn't whole cents.
    """

    dividend_id: str
    instrument_id: str
    currency: Currency
    amount_per_share: Price
    declaration_date: date
    ex_date: date
    record_date: date
    pay_date: date

    @model_validator(mode="after")
    def _dates_and_rate(self) -> Self:
        if self.ex_date != self.record_date:
            msg = "under T+1 the ex-dividend date is the record date"
            raise ValueError(msg)
        if not self.declaration_date < self.record_date < self.pay_date:
            msg = "a dividend is declared before its record date and paid after it"
            raise ValueError(msg)
        if self.amount_per_share != self.amount_per_share.quantize(_CENT):
            msg = f"the rate {self.amount_per_share} is not whole cents"
            raise ValueError(msg)
        return self


class DividendAllocation(_Frozen):
    """One account's dividend: its settled shares at the record date times the rate."""

    account_id: str
    quantity: Quantity
    amount: PositiveAmount


class DividendPaidPayload(_Frozen):
    """A dividend paid on its pay date to every account entitled at the record date.

    Cash basis (ADR 013): the depository pays the brokerage the total, and each
    entitled account is credited its allocation. Accounts holding no shares at
    the record date have no allocation.

    Raises:
        pydantic.ValidationError: An allocation isn't quantity x rate, the total
            isn't their sum, or an account appears twice.
    """

    dividend_id: str
    instrument_id: str
    currency: Currency
    amount_per_share: Price
    record_date: date
    pay_date: date
    allocations: tuple[DividendAllocation, ...] = Field(min_length=1)
    total_amount: PositiveAmount

    @model_validator(mode="after")
    def _allocations_add_up(self) -> Self:
        accounts = [a.account_id for a in self.allocations]
        if len(set(accounts)) != len(accounts):
            msg = "an account appears twice in a dividend's allocations"
            raise ValueError(msg)
        for a in self.allocations:
            if a.amount != a.quantity * self.amount_per_share:
                msg = f"{a.account_id}: {a.amount} is not {a.quantity} x {self.amount_per_share}"
                raise ValueError(msg)
        if self.total_amount != sum((a.amount for a in self.allocations), Decimal(0)):
            msg = "the total is not the sum of the allocations"
            raise ValueError(msg)
        return self


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


class AccountUpdated(_Envelope):
    """``account_updated`` event."""

    event_type: Literal[EventType.ACCOUNT_UPDATED] = EventType.ACCOUNT_UPDATED
    payload: AccountUpdatedPayload


class FxRatePublished(_Envelope):
    """``fx_rate_published`` event."""

    event_type: Literal[EventType.FX_RATE_PUBLISHED] = EventType.FX_RATE_PUBLISHED
    payload: FxRatePublishedPayload


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


class DividendDeclared(_Envelope):
    """``dividend_declared`` event."""

    event_type: Literal[EventType.DIVIDEND_DECLARED] = EventType.DIVIDEND_DECLARED
    payload: DividendDeclaredPayload


class DividendPaid(_Envelope):
    """``dividend_paid`` event."""

    event_type: Literal[EventType.DIVIDEND_PAID] = EventType.DIVIDEND_PAID
    payload: DividendPaidPayload


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
    | MarginInterestAccrued
    | AccountUpdated
    | FxRatePublished
    | DividendDeclared
    | DividendPaid,
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
    EventType.ACCOUNT_UPDATED: AccountUpdated,
    EventType.FX_RATE_PUBLISHED: FxRatePublished,
    EventType.DIVIDEND_DECLARED: DividendDeclared,
    EventType.DIVIDEND_PAID: DividendPaid,
}
"""Event model per type, used to export the JSON Schemas."""
