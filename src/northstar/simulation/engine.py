"""Deterministic brokerage simulation engine shared by every generator.

Generators (a scripted scenario file, or the seeded random generator) submit
*actions* in time order: open an account, deposit, withdraw, convert, place an
order with its fills, and publish a daily close. The engine is the simulated
brokerage. For each action it:

- applies policy: commissions and clearing fees, settlement dates, FX quotes,
  and margin interest
- keeps client balances by posting every event through the ledger's
  ``PostingEngine`` (ADR 007: one implementation), and positions through
  ``PositionBook``
- refuses actions a real brokerage would refuse: overdrawing a CASH account,
  borrowing beyond the MARGIN guard, withdrawing or converting money the
  client doesn't have, short sales, fills off a trading day, and actions out
  of time order. A refusal raises ``ScenarioError``; generators are expected
  to size actions so this never happens.
- schedules what follows from time passing: T+N settlements (one obligation
  per order and trade date, INV-06) and daily margin interest (each calendar
  day, on each currency's end-of-day debit balance).

``finish`` settles everything outstanding, stops interest at the given date,
sorts the events by timestamp (ties keep processing order), and only then
numbers the event IDs. The output depends only on the actions, the seed, and
the policy versions.
"""

import heapq
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from northstar.common.exceptions import NotATradingDayError, ScenarioError
from northstar.common.ids import EntityType, source_id
from northstar.common.money import Currency, Money
from northstar.domain.accounting import CLIENT_CASH, ChartOfAccounts, EntrySide, PostingEngine
from northstar.domain.calendar import BUSINESS_TIMEZONE, MarketCalendar, business_date
from northstar.domain.events import (
    AccountCreated,
    AccountCreatedPayload,
    AccountStatus,
    AccountType,
    AccountUpdated,
    AccountUpdatedPayload,
    AccountUpdateReason,
    CashDeposited,
    CashDepositedPayload,
    CashWithdrawn,
    CashWithdrawnPayload,
    Event,
    ExerciseStyle,
    FxConverted,
    FxConvertedPayload,
    FxRatePublished,
    FxRatePublishedPayload,
    InstrumentListed,
    InstrumentListedPayload,
    MarginInterestAccrued,
    MarginInterestAccruedPayload,
    OptionRight,
    OptionTerms,
    OrderCancelled,
    OrderCancelledPayload,
    OrderPlaced,
    OrderPlacedPayload,
    OrderType,
    PolicyVersions,
    Price,
    PriceUpdated,
    PriceUpdatedPayload,
    Side,
    TradeExecuted,
    TradeExecutedPayload,
    TradeSettled,
    TradeSettledPayload,
)
from northstar.domain.fx import FxPolicy, quote, reference_rate_on
from northstar.domain.interest import MarginPolicy, daily_interest
from northstar.domain.positions import PositionBook
from northstar.domain.settlement import AssetClass, SettlementRules, settlement_date
from northstar.domain.trading import FeeSchedule, gross_amount

LEDGER_CALENDAR_MARKET = "XTSE"
"""The ledger's business calendar (domain model §5)."""

FX_PUBLICATION_TIME_LOCAL = time(0, 0)
"""Simulation convention (†): the synthetic reference rate for day D is fixed at
the start of D, so no conversion on D is priced with a rate published after it."""

_END_OF_DAY = time(23, 59, 59)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class OptionSpec(_Frozen):
    """Option terms in a scenario: the underlying is named by instrument key."""

    underlying: str
    strike: Price
    expiry: date
    right: OptionRight
    exercise_style: ExerciseStyle


class InstrumentSpec(_Frozen):
    """A synthetic instrument. Symbols and names are invented, not real securities.

    Raises:
        pydantic.ValidationError: Option terms are missing for an option, or
            present for anything else.
    """

    key: str
    symbol: str
    name: str
    market: str
    asset_class: AssetClass
    currency: Currency
    multiplier: Annotated[int, Field(ge=1)] = 1
    option: OptionSpec | None = None

    @model_validator(mode="after")
    def _option_terms(self) -> "InstrumentSpec":
        if (self.asset_class is AssetClass.OPTION) != (self.option is not None):
            msg = f"instrument {self.key}: option terms are required for options only"
            raise ValueError(msg)
        return self


@dataclass(frozen=True, slots=True)
class Policies:
    """Every policy version the brokerage applies."""

    calendars: dict[str, MarketCalendar]
    settlement_rules: SettlementRules
    fee_schedule: FeeSchedule
    fx_policy: FxPolicy
    margin_policy: MarginPolicy
    chart: ChartOfAccounts


# --- Actions -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OpenAccount:
    """Open a client account."""

    at: datetime
    account: str
    account_type: AccountType
    base_currency: Currency


@dataclass(frozen=True, slots=True)
class UpdateAccount:
    """Change an account's type or trading status (margin approval, restriction)."""

    at: datetime
    account: str
    account_type: AccountType
    status: AccountStatus
    reason: AccountUpdateReason


@dataclass(frozen=True, slots=True)
class Deposit:
    """Client cash arrives."""

    at: datetime
    account: str
    amount: Money


@dataclass(frozen=True, slots=True)
class Withdraw:
    """Client cash leaves."""

    at: datetime
    account: str
    amount: Money


@dataclass(frozen=True, slots=True)
class Convert:
    """The client converts ``sell`` into the other currency at the client rate."""

    at: datetime
    account: str
    sell: Money


@dataclass(frozen=True, slots=True)
class Fill:
    """One execution of an order."""

    at: datetime
    quantity: Decimal
    price: Decimal


@dataclass(frozen=True, slots=True)
class PlaceOrder:
    """An order, its fills in time order, and optionally a cancellation of the rest."""

    at: datetime
    account: str
    instrument: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    fills: tuple[Fill, ...]
    limit_price: Decimal | None = None
    cancel_remainder_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ClosePrice:
    """A daily closing price, published after the close."""

    at: datetime
    instrument: str
    price_date: date
    close: Decimal


type Action = OpenAccount | UpdateAccount | Deposit | Withdraw | Convert | PlaceOrder | ClosePrice


# --- Engine --------------------------------------------------------------------


type _Make = Callable[[str, str | None], Event]
"""Builds the final event from its event ID and causation event ID."""


@dataclass(slots=True)
class _Draft:
    timestamp: datetime
    ref: str
    caused_by: str | None
    make: _Make


@dataclass(slots=True)
class _Obligation:
    settlement_id: str
    order_id: str
    account_id: str
    instrument_id: str
    side: Side
    trade_date: date
    settles_on: date
    currency: Currency
    execution_ids: list[str] = field(default_factory=list)
    quantity: Decimal = Decimal(0)
    amount: Money | None = None
    last_execution_ref: str = ""
    last_fill_at: datetime | None = None


class SimulationEngine:
    """The simulated brokerage. Submit actions in time order, then ``finish``."""

    def __init__(
        self,
        policies: Policies,
        instruments: tuple[InstrumentSpec, ...],
        fx_rates: Mapping[date, Decimal],
        seed: int,
        settlement_time_local: time,
    ) -> None:
        """Bind the engine to its policies, instrument universe, and reference rates.

        Raises:
            ScenarioError: An instrument's market has no calendar, an option's
                underlying is unknown, or an instrument key repeats.
        """
        if LEDGER_CALENDAR_MARKET not in policies.calendars:
            msg = f"the ledger calendar ({LEDGER_CALENDAR_MARKET}) is missing from the policies"
            raise ScenarioError(msg)
        self._p = policies
        self._seed = seed
        self._settlement_time = settlement_time_local
        self._fx_rates = dict(fx_rates)
        self._ledger = PostingEngine(policies.chart, policies.calendars[LEDGER_CALENDAR_MARKET])
        self._positions = PositionBook()
        self._last: dict[EntityType, int] = {}
        self._drafts: list[_Draft] = []
        self._clock: datetime | None = None
        self._balances: dict[tuple[str, Currency], Decimal] = {}
        self._accounts: dict[str, tuple[str, AccountType]] = {}
        self._account_order: list[str] = []
        self._obligations: dict[tuple[str, date], _Obligation] = {}
        self._settlement_queue: list[tuple[datetime, int, tuple[str, date]]] = []
        self._queued = 0
        self._account_types: dict[str, AccountType] = {}
        self._status: dict[str, AccountStatus] = {}
        self._next_interest_day: date | None = None
        self._finished = False
        self._broken: str | None = None

        keys = [i.key for i in instruments]
        if len(keys) != len(set(keys)):
            msg = "instrument keys must be unique"
            raise ScenarioError(msg)
        self._instruments = {i.key: i for i in instruments}
        for spec in instruments:
            if spec.market not in policies.calendars:
                msg = f"no calendar for market {spec.market} (instrument {spec.key})"
                raise ScenarioError(msg)
            if spec.option is not None and spec.option.underlying not in self._instruments:
                msg = f"option {spec.key} names unknown underlying {spec.option.underlying!r}"
                raise ScenarioError(msg)
        self._instrument_ids = {
            spec.key: self._next_id(EntityType.INSTRUMENT) for spec in instruments
        }
        self._listed = False

    # -- queries used by generators to size actions ---------------------------

    def cash_balance(self, account: str, currency: Currency) -> Money:
        """The client's 2100 balance in ``currency`` (negative = a loan)."""
        account_id, _ = self._account(account)
        return Money(self._balances.get((account_id, currency), Decimal(0)), currency)

    def debit_cad(self, account: str, on: date) -> Money:
        """Total loan across currencies in CAD at the reference rate on ``on``."""
        account_id, _ = self._account(account)
        return self._debit_cad(account_id, on)

    def trade_date_quantity(self, account: str, instrument: str) -> Decimal:
        """Units the account owns on a trade-date basis."""
        account_id, _ = self._account(account)
        return self._positions.trade_date_quantity(account_id, self._instrument_ids[instrument])

    def account_type(self, account: str) -> AccountType:
        """The account's current type (it changes on a margin approval)."""
        return self._account(account)[1]

    def trading_restricted(self, account: str) -> bool:
        """Whether the account is barred from placing orders."""
        account_id, _ = self._account(account)
        return self._status[account_id] is AccountStatus.TRADING_RESTRICTED

    def reference_rate(self, on: date) -> Decimal:
        """The reference USD/CAD rate that applies on ``on`` (policy fallback)."""
        return reference_rate_on(on, self._fx_rates)[0]

    # -- actions -----------------------------------------------------------------

    def submit(self, action: Action) -> None:
        """Process one action.

        Raises:
            ScenarioError: The action is out of time order, names an unknown
                account or instrument, or a brokerage would refuse it.
            NotATradingDayError: A fill is not on a trading day.
            PolicyNotFoundError, CalendarCoverageError: Policy or calendar gaps.
        """
        self._require_usable()
        completed = False
        try:
            self._advance(action.at)
            if not self._listed:
                self._list_instruments(action.at)
            match action:
                case OpenAccount():
                    self._open(action)
                case UpdateAccount():
                    self._update(action)
                case Deposit():
                    self._deposit(action)
                case Withdraw():
                    self._withdraw(action)
                case Convert():
                    self._convert(action)
                case PlaceOrder():
                    self._order(action)
                case ClosePrice():
                    self._close(action)
            completed = True
        finally:
            if not completed:
                # A refused action may have applied part of its effects, so the
                # state can no longer be trusted. The error itself propagates.
                self._broken = f"the {type(action).__name__} at {action.at.isoformat()} was refused"

    def finish(self, interest_until: date) -> tuple[Event, ...]:
        """Accrue interest through ``interest_until``, settle everything, number the events.

        Raises:
            ScenarioError: Called twice.
        """
        self._require_usable()
        self._accrue_interest_through(interest_until)
        self._publish_fx_rates(interest_until)
        while self._settlement_queue:
            _, _, key = heapq.heappop(self._settlement_queue)
            self._settle(key)
        self._finished = True
        ordered = sorted(self._drafts, key=lambda d: d.timestamp)  # stable on ties
        event_ids: dict[str, str] = {}
        events: list[Event] = []
        for draft in ordered:
            event_id = self._next_id(EntityType.EVENT)
            event_ids[draft.ref] = event_id
            cause = event_ids[draft.caused_by] if draft.caused_by is not None else None
            events.append(draft.make(event_id, cause))
        return tuple(events)

    # -- internals ---------------------------------------------------------------

    def _require_usable(self) -> None:
        if self._broken is not None:
            msg = f"the engine cannot continue: {self._broken}"
            raise ScenarioError(msg)
        if self._finished:
            msg = "the simulation is finished"
            raise ScenarioError(msg)

    def _next_id(self, entity: EntityType) -> str:
        sequence = self._last.get(entity, 0) + 1
        self._last[entity] = sequence
        return source_id(entity, self._seed, sequence)

    def _account(self, key: str) -> tuple[str, AccountType]:
        try:
            return self._accounts[key]
        except KeyError as error:
            msg = f"unknown account {key!r}"
            raise ScenarioError(msg) from error

    def _instrument(self, key: str) -> tuple[str, InstrumentSpec]:
        if key not in self._instruments:
            msg = f"unknown instrument {key!r}"
            raise ScenarioError(msg)
        return self._instrument_ids[key], self._instruments[key]

    def _advance(self, to: datetime) -> None:
        if self._clock is not None and to < self._clock:
            msg = f"action at {to.isoformat()} is before {self._clock.isoformat()}"
            raise ScenarioError(msg)
        # Interest for every day that has fully ended, then due settlements, in time order.
        while True:
            interest_at = self._interest_time()
            settle_at = self._settlement_queue[0][0] if self._settlement_queue else None
            due = [t for t in (interest_at, settle_at) if t is not None and t <= to]
            if not due:
                break
            if interest_at is not None and interest_at == min(due):
                self._accrue_day()
            else:
                _, _, key = heapq.heappop(self._settlement_queue)
                self._settle(key)
        self._clock = to

    def _interest_time(self) -> datetime | None:
        if self._next_interest_day is None:
            return None
        return _local(self._next_interest_day, _END_OF_DAY)

    def _record(self, draft: _Draft, event_for_ledger: Event | None) -> None:
        """Keep the draft, and apply its ledger and position effects now."""
        if event_for_ledger is not None:
            self._positions.apply(event_for_ledger)
            for batch in self._ledger.post_events([event_for_ledger]):
                for line in batch.lines:
                    if line.gl_account == CLIENT_CASH and line.client_account_id is not None:
                        signed = (
                            line.amount.amount
                            if line.side is EntrySide.CREDIT
                            else -line.amount.amount
                        )
                        key = (line.client_account_id, line.amount.currency)
                        self._balances[key] = self._balances.get(key, Decimal(0)) + signed
        self._drafts.append(draft)

    def _list_instruments(self, at: datetime) -> None:
        self._listed = True
        for key, spec in self._instruments.items():
            instrument_id = self._instrument_ids[key]
            option = None
            if spec.option is not None:
                option = OptionTerms(
                    underlying_instrument_id=self._instrument_ids[spec.option.underlying],
                    strike=spec.option.strike,
                    expiry=spec.option.expiry,
                    right=spec.option.right,
                    exercise_style=spec.option.exercise_style,
                )
            payload = InstrumentListedPayload(
                instrument_id=instrument_id,
                symbol=spec.symbol,
                name=spec.name,
                market=spec.market,
                asset_class=spec.asset_class,
                currency=spec.currency,
                multiplier=spec.multiplier,
                option=option,
            )
            self._record(
                _Draft(
                    at, instrument_id, None, _maker(InstrumentListed, at, instrument_id, payload)
                ),
                None,
            )

    def _open(self, action: OpenAccount) -> None:
        if action.account in self._accounts:
            msg = f"account {action.account!r} is already open"
            raise ScenarioError(msg)
        account_id = self._next_id(EntityType.ACCOUNT)
        self._accounts[action.account] = (account_id, action.account_type)
        self._account_types[account_id] = action.account_type
        self._status[account_id] = AccountStatus.ACTIVE
        self._account_order.append(account_id)
        if self._next_interest_day is None:
            self._next_interest_day = business_date(action.at)
        payload = AccountCreatedPayload(
            account_id=account_id,
            account_type=action.account_type,
            base_currency=action.base_currency,
        )
        self._record(
            _Draft(
                action.at, account_id, None, _maker(AccountCreated, action.at, account_id, payload)
            ),
            None,
        )

    def _update(self, action: UpdateAccount) -> None:
        account_id, current_type = self._account(action.account)
        if (action.account_type, action.status) == (current_type, self._status[account_id]):
            msg = f"account {action.account!r} update changes nothing"
            raise ScenarioError(msg)
        has_loan = any(b < 0 for (a, _), b in self._balances.items() if a == account_id)
        if has_loan and (current_type, action.account_type) == (
            AccountType.MARGIN,
            AccountType.CASH,
        ):
            msg = f"account {action.account!r} cannot become CASH while it has a loan"
            raise ScenarioError(msg)
        self._accounts[action.account] = (account_id, action.account_type)
        self._account_types[account_id] = action.account_type
        self._status[account_id] = action.status
        payload = AccountUpdatedPayload(
            account_id=account_id,
            account_type=action.account_type,
            status=action.status,
            reason=action.reason,
        )
        ref = f"acu:{account_id}:{action.at.isoformat()}"
        self._record(
            _Draft(action.at, ref, None, _maker(AccountUpdated, action.at, account_id, payload)),
            None,
        )

    def _publish_fx_rates(self, until: date) -> None:
        """One ``fx_rate_published`` per reference rate dated on or before ``until``."""
        for rate_date in sorted(d for d in self._fx_rates if d <= until):
            at = _local(rate_date, FX_PUBLICATION_TIME_LOCAL)
            payload = FxRatePublishedPayload(
                base_currency=Currency.USD,
                quote_currency=Currency.CAD,
                rate_date=rate_date,
                rate=self._fx_rates[rate_date],
                rate_source=self._p.fx_policy.rate_source,
                fx_policy_version=self._p.fx_policy.version,
            )
            ref = f"fxr:USDCAD:{rate_date.isoformat()}"
            self._record(
                _Draft(at, ref, None, _maker(FxRatePublished, at, ref, payload)),
                None,
            )

    def _deposit(self, action: Deposit) -> None:
        account_id, _ = self._account(action.account)
        cm_id = self._next_id(EntityType.CASH_MOVEMENT)
        payload = CashDepositedPayload(
            cash_movement_id=cm_id,
            account_id=account_id,
            amount=action.amount.amount,
            currency=action.amount.currency,
        )
        self._record(
            _Draft(action.at, cm_id, None, _maker(CashDeposited, action.at, account_id, payload)),
            CashDeposited(**_envelope(action.at, account_id), payload=payload),
        )

    def _withdraw(self, action: Withdraw) -> None:
        account_id, _ = self._account(action.account)
        available = self._balances.get((account_id, action.amount.currency), Decimal(0))
        if action.amount.amount > available:
            msg = (
                f"withdrawal of {action.amount.amount} {action.amount.currency} exceeds the "
                f"{available} available in {account_id}"
            )
            raise ScenarioError(msg)
        cm_id = self._next_id(EntityType.CASH_MOVEMENT)
        payload = CashWithdrawnPayload(
            cash_movement_id=cm_id,
            account_id=account_id,
            amount=action.amount.amount,
            currency=action.amount.currency,
        )
        self._record(
            _Draft(action.at, cm_id, None, _maker(CashWithdrawn, action.at, account_id, payload)),
            CashWithdrawn(**_envelope(action.at, account_id), payload=payload),
        )

    def _convert(self, action: Convert) -> None:
        account_id, _ = self._account(action.account)
        available = self._balances.get((account_id, action.sell.currency), Decimal(0))
        if action.sell.amount > available:
            msg = (
                f"conversion of {action.sell.amount} {action.sell.currency} exceeds the "
                f"{available} available in {account_id}"
            )
            raise ScenarioError(msg)
        day = business_date(action.at)
        rate, rate_date = reference_rate_on(day, self._fx_rates)
        buy_currency = Currency.USD if action.sell.currency is Currency.CAD else Currency.CAD
        q = quote(action.sell, buy_currency, rate, self._p.fx_policy.spread)
        fx_id = self._next_id(EntityType.FX_CONVERSION)
        payload = FxConvertedPayload(
            fx_conversion_id=fx_id,
            account_id=account_id,
            sell_amount=q.sell.amount,
            sell_currency=q.sell.currency,
            buy_amount=q.buy.amount,
            buy_currency=q.buy.currency,
            reference_rate=q.reference_rate,
            client_rate=q.client_rate,
            rate_date=rate_date,
            rate_source=self._p.fx_policy.rate_source,
            fx_policy_version=self._p.fx_policy.version,
        )
        self._record(
            _Draft(action.at, fx_id, None, _maker(FxConverted, action.at, account_id, payload)),
            FxConverted(**_envelope(action.at, account_id), payload=payload),
        )

    def _order(self, action: PlaceOrder) -> None:
        account_id, account_type = self._account(action.account)
        if self._status[account_id] is AccountStatus.TRADING_RESTRICTED:
            msg = f"account {action.account!r} is restricted from trading"
            raise ScenarioError(msg)
        instrument_id, spec = self._instrument(action.instrument)
        calendar = self._p.calendars[spec.market]
        if not action.fills:
            msg = "an order needs at least one fill"
            raise ScenarioError(msg)
        filled = sum((f.quantity for f in action.fills), Decimal(0))
        if filled > action.quantity:
            msg = f"fills ({filled}) exceed the order quantity ({action.quantity})"
            raise ScenarioError(msg)
        times = [action.at, *(f.at for f in action.fills)]
        if times != sorted(times):
            msg = "fills must not precede the order and must be in time order"
            raise ScenarioError(msg)
        versions = PolicyVersions(
            calendar=calendar.version,
            settlement_rules=self._p.settlement_rules.version,
            fee_schedule=self._p.fee_schedule.version,
        )
        order_id = self._next_id(EntityType.ORDER)
        order_payload = OrderPlacedPayload(
            order_id=order_id,
            account_id=account_id,
            instrument_id=instrument_id,
            market=spec.market,
            side=action.side,
            order_type=action.order_type,
            quantity=action.quantity,
            limit_price=action.limit_price,
            currency=spec.currency,
        )
        self._record(
            _Draft(
                action.at, order_id, None, _maker(OrderPlaced, action.at, order_id, order_payload)
            ),
            None,
        )
        for fill in action.fills:
            self._fill(
                action, fill, order_id, account_id, account_type, instrument_id, spec, versions
            )
        remainder = action.quantity - filled
        if action.cancel_remainder_at is not None:
            if remainder <= 0:
                msg = "nothing is left to cancel"
                raise ScenarioError(msg)
            at = action.cancel_remainder_at
            if at < action.fills[-1].at:
                msg = "a cancellation cannot precede the last fill"
                raise ScenarioError(msg)
            cancel_ref = f"{order_id}:cancel"
            payload = OrderCancelledPayload(
                order_id=order_id, account_id=account_id, cancelled_quantity=remainder
            )
            self._record(
                _Draft(at, cancel_ref, order_id, _maker(OrderCancelled, at, order_id, payload)),
                None,
            )
        self._clock = action.cancel_remainder_at or action.fills[-1].at

    def _fill(
        self,
        action: PlaceOrder,
        fill: Fill,
        order_id: str,
        account_id: str,
        account_type: AccountType,
        instrument_id: str,
        spec: InstrumentSpec,
        versions: PolicyVersions,
    ) -> None:
        trade_date = business_date(fill.at)
        calendar = self._p.calendars[spec.market]
        if not calendar.is_trading_day(trade_date):
            msg = (
                f"fill at {fill.at.isoformat()} is on {trade_date}, not a {spec.market} trading day"
            )
            raise NotATradingDayError(msg)
        key = (order_id, trade_date)
        if key not in self._obligations:
            settles_on = settlement_date(
                trade_date, spec.market, spec.asset_class, self._p.settlement_rules, calendar
            )
            settlement_id = self._next_id(EntityType.SETTLEMENT)
            self._obligations[key] = _Obligation(
                settlement_id=settlement_id,
                order_id=order_id,
                account_id=account_id,
                instrument_id=instrument_id,
                side=action.side,
                trade_date=trade_date,
                settles_on=settles_on,
                currency=spec.currency,
            )
            self._queued += 1
            heapq.heappush(
                self._settlement_queue,
                (_local(settles_on, self._settlement_time), self._queued, key),
            )
        obligation = self._obligations[key]
        execution_id = self._next_id(EntityType.EXECUTION)
        fees = self._p.fee_schedule
        payload = TradeExecutedPayload(
            execution_id=execution_id,
            order_id=order_id,
            account_id=account_id,
            instrument_id=instrument_id,
            market=spec.market,
            asset_class=spec.asset_class,
            side=action.side,
            quantity=fill.quantity,
            price=fill.price,
            multiplier=spec.multiplier,
            currency=spec.currency,
            commission=fees.commission(
                spec.market, spec.currency, trade_date, fill.quantity
            ).amount,
            clearing_fee=fees.clearing_fee(
                spec.market, spec.currency, trade_date, fill.quantity
            ).amount,
            trade_date=trade_date,
            settlement_date=obligation.settles_on,
            settlement_id=obligation.settlement_id,
            policy_versions=versions,
        )
        self._record(
            _Draft(
                fill.at, execution_id, order_id, _maker(TradeExecuted, fill.at, order_id, payload)
            ),
            TradeExecuted(**_envelope(fill.at, order_id), payload=payload),
        )
        self._require_funds(account_id, account_type, trade_date)
        gross = gross_amount(fill.quantity, fill.price, spec.multiplier, spec.currency)
        obligation.execution_ids.append(execution_id)
        obligation.quantity += fill.quantity
        obligation.amount = gross if obligation.amount is None else obligation.amount + gross
        obligation.last_execution_ref = execution_id
        obligation.last_fill_at = fill.at

    def _require_funds(self, account_id: str, account_type: AccountType, on: date) -> None:
        if account_type is AccountType.CASH:
            for (acct, currency), balance in self._balances.items():
                if acct == account_id and balance < 0:
                    msg = f"CASH account {account_id} would be overdrawn in {currency} ({balance})"
                    raise ScenarioError(msg)
            return
        guard = self._p.margin_policy.max_debit_balance
        debit = self._debit_cad(account_id, on)
        if debit.amount > guard.amount:
            msg = (
                f"MARGIN account {account_id} would borrow {debit.amount} CAD, over the "
                f"{guard.amount} simulator guard (not a margin requirement)"
            )
            raise ScenarioError(msg)

    def _debit_cad(self, account_id: str, on: date) -> Money:
        total = Decimal(0)
        for (acct, currency), balance in self._balances.items():
            if acct != account_id or balance >= 0:
                continue
            loan = -balance
            total += (
                loan
                if currency is Currency.CAD
                else loan * reference_rate_on(on, self._fx_rates)[0]
            )
        return Money(total, Currency.CAD).quantize()

    def _settle(self, key: tuple[str, date]) -> None:
        ob = self._obligations.pop(key)
        if (
            ob.amount is None or ob.last_fill_at is None
        ):  # pragma: no cover - scheduled on first fill
            msg = f"obligation {ob.settlement_id} has no executions"
            raise ScenarioError(msg)
        at = _local(ob.settles_on, self._settlement_time)
        if at <= ob.last_fill_at:
            msg = f"settlement {ob.settlement_id} would be stamped before its last fill"
            raise ScenarioError(msg)
        payload = TradeSettledPayload(
            settlement_id=ob.settlement_id,
            order_id=ob.order_id,
            account_id=ob.account_id,
            instrument_id=ob.instrument_id,
            side=ob.side,
            trade_date=ob.trade_date,
            settlement_date=ob.settles_on,
            quantity=ob.quantity,
            amount=ob.amount.amount,
            currency=ob.currency,
            execution_ids=tuple(ob.execution_ids),
        )
        self._record(
            _Draft(
                at,
                ob.settlement_id,
                ob.last_execution_ref,
                _maker(TradeSettled, at, ob.order_id, payload),
            ),
            TradeSettled(**_envelope(at, ob.order_id), payload=payload),
        )

    def _accrue_interest_through(self, until: date) -> None:
        while self._next_interest_day is not None and self._next_interest_day <= until:
            at = _local(self._next_interest_day, _END_OF_DAY)
            while self._settlement_queue and self._settlement_queue[0][0] <= at:
                _, _, key = heapq.heappop(self._settlement_queue)
                self._settle(key)
            self._accrue_day()

    def _accrue_day(self) -> None:
        day = self._next_interest_day
        if day is None:  # pragma: no cover - guarded by the callers
            return
        at = _local(day, _END_OF_DAY)
        policy = self._p.margin_policy
        for account_id in self._account_order:
            if self._account_types[account_id] is not AccountType.MARGIN:
                continue
            for currency in Currency:
                balance = self._balances.get((account_id, currency), Decimal(0))
                if balance >= 0:
                    continue
                debit = Money(-balance, currency)
                amount = daily_interest(debit, policy.annual_rate)
                if amount.amount == 0:
                    continue
                ref = f"int:{account_id}:{currency}:{day.isoformat()}"
                payload = MarginInterestAccruedPayload(
                    account_id=account_id,
                    accrual_date=day,
                    currency=currency,
                    debit_balance=debit.quantize().amount,
                    annual_rate=policy.annual_rate,
                    day_count=policy.day_count,
                    amount=amount.amount,
                    margin_policy_version=policy.version,
                )
                self._record(
                    _Draft(at, ref, None, _maker(MarginInterestAccrued, at, account_id, payload)),
                    MarginInterestAccrued(**_envelope(at, account_id), payload=payload),
                )
        self._next_interest_day = day + timedelta(days=1)

    def _close(self, action: ClosePrice) -> None:
        instrument_id, spec = self._instrument(action.instrument)
        ref = f"px:{instrument_id}:{action.price_date.isoformat()}"
        payload = PriceUpdatedPayload(
            instrument_id=instrument_id,
            price_date=action.price_date,
            close_price=action.close,
            currency=spec.currency,
            source="synthetic",
        )
        self._record(
            _Draft(action.at, ref, None, _maker(PriceUpdated, action.at, instrument_id, payload)),
            None,
        )


def _local(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=BUSINESS_TIMEZONE).astimezone(UTC)


def _envelope(at: datetime, correlation_id: str) -> dict[str, Any]:
    """Envelope fields for the provisional event the ledger and positions see."""
    return {
        "event_id": "evt_pending",
        "event_timestamp": at,
        "recorded_at": at,
        "correlation_id": correlation_id,
    }


def _maker(model: type[Any], at: datetime, correlation_id: str, payload: BaseModel) -> _Make:
    def make(event_id: str, causation_id: str | None) -> Event:
        event: Event = model(
            event_id=event_id,
            event_timestamp=at,
            recorded_at=at,
            correlation_id=correlation_id,
            causation_id=causation_id,
            payload=payload,
        )
        return event

    return make
