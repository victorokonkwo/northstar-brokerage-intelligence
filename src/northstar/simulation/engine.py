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
from collections import defaultdict
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
from northstar.domain.corporate_actions import OptionExpiryPolicy, dividend_allocations
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
    DividendAllocation,
    DividendDeclared,
    DividendDeclaredPayload,
    DividendPaid,
    DividendPaidPayload,
    Event,
    ExerciseReason,
    ExerciseSettled,
    ExerciseSettledPayload,
    ExerciseStyle,
    ExpiryReason,
    FxConverted,
    FxConvertedPayload,
    FxRatePublished,
    FxRatePublishedPayload,
    InstrumentListed,
    InstrumentListedPayload,
    MarginInterestAccrued,
    MarginInterestAccruedPayload,
    OptionExercised,
    OptionExercisedPayload,
    OptionExpired,
    OptionExpiredPayload,
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
from northstar.domain.market_risk.policy import RiskFreeRates
from northstar.domain.positions import PositionBook
from northstar.domain.settlement import AssetClass, SettlementRules, settlement_date
from northstar.domain.trading import FeeSchedule, gross_amount

LEDGER_CALENDAR_MARKET = "XTSE"
"""The ledger's business calendar (domain model §5)."""

FX_PUBLICATION_TIME_LOCAL = time(0, 0)
"""Simulation convention (†): the synthetic reference rate for day D is fixed at
the start of D, so no conversion on D is priced with a rate published after it."""

_END_OF_DAY = time(23, 59, 59)
_DIVIDEND_PAYMENT_TIME = time(9, 0)
"""Dividends are paid at 09:00 Toronto on the pay date (a simulation convention)."""


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
    option_expiry: OptionExpiryPolicy
    """What happens to clients' options at expiry (roadmap 5C-b, ADR 013)."""
    chart: ChartOfAccounts
    risk_free_rates: RiskFreeRates
    """The rates the synthetic market prices options with (config/risk/rates.yaml)."""


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
class DeclareDividend:
    """An issuer declares a cash dividend on an equity (roadmap 5C-a, ADR 013).

    ``record_date`` is also the ex-dividend date (T+1). Entitlements are fixed
    from settled holdings at the end of the record date, and the dividend is
    paid at the start of ``pay_date``.
    """

    at: datetime
    instrument: str
    amount_per_share: Decimal
    record_date: date
    pay_date: date


@dataclass(frozen=True, slots=True)
class ExerciseOption:
    """A client exercises settled contracts of a long American option before expiry."""

    at: datetime
    account: str
    option: str
    contracts: Decimal


@dataclass(frozen=True, slots=True)
class ProcessExpiry:
    """Process an expiry date's options after the close (ADR 013).

    Every client option expiring that day is exercised, if in the money by the
    policy threshold, or expires.
    """

    at: datetime
    expiry_date: date


@dataclass(frozen=True, slots=True)
class ClosePrice:
    """A daily closing price, published after the close."""

    at: datetime
    instrument: str
    price_date: date
    close: Decimal


type Action = (
    OpenAccount
    | UpdateAccount
    | Deposit
    | Withdraw
    | Convert
    | PlaceOrder
    | DeclareDividend
    | ExerciseOption
    | ProcessExpiry
    | ClosePrice
)


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
class _Dividend:
    dividend_id: str
    instrument_id: str
    currency: Currency
    amount_per_share: Decimal
    record_date: date
    pay_date: date
    allocations: tuple[DividendAllocation, ...] | None = None  # fixed at the record date


@dataclass(frozen=True, slots=True)
class _Exercise:
    ref: str
    payload: OptionExercisedPayload
    settles_at: datetime


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
        # The same balances indexed by account, so funds checks look at one
        # account's currencies instead of scanning every client (O(1) per order).
        self._by_account: dict[str, dict[Currency, Decimal]] = {}
        self._accounts: dict[str, tuple[str, AccountType]] = {}
        self._account_order: list[str] = []
        self._obligations: dict[tuple[str, date], _Obligation] = {}
        self._settlement_queue: list[tuple[datetime, int, tuple[str, date]]] = []
        # Corporate-event steps: (when, sequence, step, dividend_id), step "record" or "pay".
        self._corporate_queue: list[tuple[datetime, int, str, str]] = []
        self._dividends: dict[str, _Dividend] = {}
        self._exercises: dict[str, _Exercise] = {}
        self._closes: dict[tuple[str, date], Decimal] = {}  # (instrument key, date)
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

    def settled_quantity(self, account: str, instrument: str) -> Decimal:
        """Units the account owns on a settlement-date basis."""
        account_id, _ = self._account(account)
        return self._positions.settled_quantity(account_id, self._instrument_ids[instrument])

    def exercise_shortfall(
        self, account: str, option: str, contracts: Decimal, on: date
    ) -> str | None:
        """Why the account can't exercise ``contracts`` of ``option`` now, or None if it can.

        It needs the contracts settled; for a put, the shares to deliver (long
        only); for a call, the strike amount: in cash for a CASH account, within
        the borrowing guard for a MARGIN account.
        """
        account_id, _ = self._account(account)
        _, spec = self._instrument(option)
        return self._shortfall(account_id, spec, contracts, on, {}, {})

    def expiry_close_outs(
        self,
        day: date,
        underlying_closes: Mapping[str, Decimal],
        option_closes: Mapping[str, Decimal],
    ) -> list[tuple[str, str, Decimal]]:
        """What the brokerage must sell at the close on an expiry day (ADR 013).

        The options expiring ``day`` that the policy would exercise, but that their
        holder couldn't exercise after everything processed before them, in the
        order expiry processing takes them (instrument, then account): an earlier
        exercise uses up cash or borrowing room or delivers shares, and an earlier
        close-out sale adds its proceeds less commission. Restricted accounts can't
        sell, so their options are left out (they expire NOT_EXERCISABLE).
        Returns (account, option, contracts) to sell, at ``option_closes``.
        """
        policy = self._p.option_expiry
        if not policy.close_out_uncovered:
            return []
        holders = {v: k for k, (v, _) in self._accounts.items()}
        cash: dict[tuple[str, Currency], Decimal] = defaultdict(Decimal)
        shares: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        sells: list[tuple[str, str, Decimal]] = []
        for key, spec in self._instruments.items():
            terms = spec.option
            if terms is None or terms.expiry != day:
                continue
            close = underlying_closes.get(terms.underlying)
            if close is None or not policy.exercises_at_expiry(
                terms.right, terms.strike, close, day
            ):
                continue
            option_id = self._instrument_ids[key]
            underlying_id = self._instrument_ids[terms.underlying]
            for (account_id, instrument_id), (owned, _) in self._positions.positions().items():
                if instrument_id != option_id or owned <= 0:
                    continue
                if self._status[account_id] is AccountStatus.TRADING_RESTRICTED:
                    continue
                if self._shortfall(account_id, spec, owned, day, cash, shares) is None:
                    amount = terms.strike * owned * spec.multiplier
                    if terms.right is OptionRight.CALL:
                        cash[(account_id, spec.currency)] -= amount
                        shares[(account_id, underlying_id)] += owned * spec.multiplier
                    else:
                        cash[(account_id, spec.currency)] += amount
                        shares[(account_id, underlying_id)] -= owned * spec.multiplier
                    continue
                price = option_closes[key]
                fees = self._p.fee_schedule
                proceeds = gross_amount(owned, price, spec.multiplier, spec.currency)
                commission = fees.commission(spec.market, spec.currency, day, owned)
                cash[(account_id, spec.currency)] += proceeds.amount - commission.amount
                sells.append((holders[account_id], key, owned))
        return sells

    def _shortfall(
        self,
        account_id: str,
        spec: InstrumentSpec,
        contracts: Decimal,
        on: date,
        cash_delta: Mapping[tuple[str, Currency], Decimal],
        shares_delta: Mapping[tuple[str, str], Decimal],
    ) -> str | None:
        """``exercise_shortfall`` after hypothetical cash and share changes (expiry planning)."""
        if spec.option is None:
            return f"{spec.key!r} is not an option"
        option_id = self._instrument_ids[spec.key]
        if self._positions.settled_quantity(account_id, option_id) < contracts:
            return "the contracts are not all settled"
        shares = contracts * spec.multiplier
        underlying_id = self._instrument_ids[spec.option.underlying]
        if spec.option.right is OptionRight.PUT:
            held = self._positions.trade_date_quantity(account_id, underlying_id)
            if held + shares_delta.get((account_id, underlying_id), Decimal(0)) < shares:
                return "the account doesn't hold the shares to deliver"
            return None
        cost = spec.option.strike * shares
        balances = dict(self._by_account.get(account_id, {}))
        for (owner, currency), delta in cash_delta.items():
            if owner == account_id:
                balances[currency] = balances.get(currency, Decimal(0)) + delta
        balance = balances.get(spec.currency, Decimal(0))
        if self._account_types[account_id] is AccountType.CASH:
            return None if balance >= cost else "the CASH account can't pay the strike"
        balances[spec.currency] = balance - cost
        if self._debit_cad_of(balances, on).amount > self._p.margin_policy.max_debit_balance.amount:
            return "paying the strike would borrow over the guard"
        return None

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
                case DeclareDividend():
                    self._declare_dividend(action)
                case ExerciseOption():
                    self._exercise_early(action)
                case ProcessExpiry():
                    self._process_expiry(action)
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
        while self._next_scheduled() is not None:
            self._run_next_scheduled()
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
        # Interest for every day that has fully ended, then due settlements and
        # corporate-event steps, in time order.
        while True:
            interest_at = self._interest_time()
            scheduled_at = self._next_scheduled()
            due = [t for t in (interest_at, scheduled_at) if t is not None and t <= to]
            if not due:
                break
            if interest_at is not None and interest_at == min(due):
                self._accrue_day()
            else:
                self._run_next_scheduled()
        self._clock = to

    def _next_scheduled(self) -> datetime | None:
        """When the next settlement or corporate-event step is due."""
        heads = [q[0][0] for q in (self._settlement_queue, self._corporate_queue) if q]
        return min(heads, default=None)

    def _run_next_scheduled(self) -> None:
        """Run the next settlement or corporate-event step (a settlement first on a tie)."""
        settle_at = self._settlement_queue[0][0] if self._settlement_queue else None
        corporate_at = self._corporate_queue[0][0] if self._corporate_queue else None
        if settle_at is not None and (corporate_at is None or settle_at <= corporate_at):
            _, _, key = heapq.heappop(self._settlement_queue)
            self._settle(key)
        elif corporate_at is not None:
            _, _, step, dividend_id = heapq.heappop(self._corporate_queue)
            if step == "record":
                self._fix_entitlements(dividend_id)
            elif step == "pay":
                self._pay_dividend(dividend_id)
            else:
                self._settle_exercise(dividend_id)  # the step's key is the exercise_id

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
                        self._by_account.setdefault(key[0], {})[key[1]] = self._balances[key]
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
        has_loan = any(b < 0 for b in self._by_account.get(account_id, {}).values())
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
            for currency, balance in self._by_account.get(account_id, {}).items():
                if balance < 0:
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
        return self._debit_cad_of(self._by_account.get(account_id, {}), on)

    def _debit_cad_of(self, balances: Mapping[Currency, Decimal], on: date) -> Money:
        total = Decimal(0)
        for currency, balance in balances.items():
            if balance >= 0:
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
            while (due := self._next_scheduled()) is not None and due <= at:
                self._run_next_scheduled()
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

    def _declare_dividend(self, action: DeclareDividend) -> None:
        instrument_id, spec = self._instrument(action.instrument)
        if spec.asset_class is not AssetClass.EQUITY:
            msg = f"dividends are declared on equities, not {action.instrument!r}"
            raise ScenarioError(msg)
        calendar = self._p.calendars[spec.market]
        record = action.record_date
        if not (calendar.is_trading_day(record) and calendar.is_settlement_day(record)):
            msg = f"the record (ex-)date {record} is not a {spec.market} business day"
            raise ScenarioError(msg)
        if not calendar.is_settlement_day(action.pay_date):
            msg = f"the pay date {action.pay_date} is not a {spec.market} settlement day"
            raise ScenarioError(msg)
        dividend_id = self._next_id(EntityType.DIVIDEND)
        payload = DividendDeclaredPayload(
            dividend_id=dividend_id,
            instrument_id=instrument_id,
            currency=spec.currency,
            amount_per_share=action.amount_per_share,
            declaration_date=business_date(action.at),
            ex_date=record,
            record_date=record,
            pay_date=action.pay_date,
        )
        self._record(
            _Draft(
                action.at,
                dividend_id,
                None,
                _maker(DividendDeclared, action.at, dividend_id, payload),
            ),
            None,
        )
        self._dividends[dividend_id] = _Dividend(
            dividend_id,
            instrument_id,
            spec.currency,
            action.amount_per_share,
            record,
            action.pay_date,
        )
        for step, at in (
            ("record", _local(record, _END_OF_DAY)),
            ("pay", _local(action.pay_date, _DIVIDEND_PAYMENT_TIME)),
        ):
            self._queued += 1
            heapq.heappush(self._corporate_queue, (at, self._queued, step, dividend_id))

    def _fix_entitlements(self, dividend_id: str) -> None:
        """At the end of the record date: each account's settled shares (ADR 013)."""
        dividend = self._dividends[dividend_id]
        settled = {
            account_id: quantities[1]
            for (account_id, instrument_id), quantities in self._positions.positions().items()
            if instrument_id == dividend.instrument_id
        }
        dividend.allocations = dividend_allocations(dividend.amount_per_share, settled)

    def _pay_dividend(self, dividend_id: str) -> None:
        dividend = self._dividends[dividend_id]
        if dividend.allocations is None:  # pragma: no cover - the record step always runs first
            msg = f"{dividend_id} is paid before its record date"
            raise ScenarioError(msg)
        if not dividend.allocations:
            return  # nobody held the shares at the record date: nothing to pay
        at = _local(dividend.pay_date, _DIVIDEND_PAYMENT_TIME)
        payload = DividendPaidPayload(
            dividend_id=dividend_id,
            instrument_id=dividend.instrument_id,
            currency=dividend.currency,
            amount_per_share=dividend.amount_per_share,
            record_date=dividend.record_date,
            pay_date=dividend.pay_date,
            allocations=dividend.allocations,
            total_amount=sum((a.amount for a in dividend.allocations), Decimal(0)),
        )
        self._record(
            _Draft(
                at,
                f"{dividend_id}:paid",
                dividend_id,
                _maker(DividendPaid, at, dividend_id, payload),
            ),
            DividendPaid(**_envelope(at, dividend_id), payload=payload),
        )

    def _exercise_early(self, action: ExerciseOption) -> None:
        _, spec = self._instrument(action.option)
        if spec.option is None or spec.option.exercise_style is not ExerciseStyle.AMERICAN:
            msg = f"only an American option can be exercised early, not {action.option!r}"
            raise ScenarioError(msg)
        if business_date(action.at) >= spec.option.expiry:
            msg = f"{action.option} expires {spec.option.expiry}: expiry processing exercises it"
            raise ScenarioError(msg)
        self._exercise(action.at, action.account, action.option, action.contracts, None)

    def _process_expiry(self, action: ProcessExpiry) -> None:
        policy = self._p.option_expiry
        day = action.expiry_date
        local = action.at.astimezone(BUSINESS_TIMEZONE)
        if local.date() != day or local.time() < policy.processing_time_local:
            msg = f"expiry of {day} is processed at {policy.processing_time_local} that day"
            raise ScenarioError(msg)
        holders = {v: k for k, (v, _) in self._accounts.items()}  # account_id -> key
        for key, spec in self._instruments.items():
            if spec.option is None or spec.option.expiry != day:
                continue
            close = self._closes.get((spec.option.underlying, day))
            if close is None:
                msg = f"no {day} close of {spec.option.underlying} to decide {key}'s expiry"
                raise ScenarioError(msg)
            option_id = self._instrument_ids[key]
            exercise = policy.exercises_at_expiry(spec.option.right, spec.option.strike, close, day)
            for (account_id, instrument_id), (owned, _) in self._positions.positions().items():
                if instrument_id != option_id or owned <= 0:
                    continue
                account = holders[account_id]
                if not exercise:
                    reason = ExpiryReason.OUT_OF_THE_MONEY
                elif (
                    self.exercise_shortfall(account, key, owned, day) is not None
                    and self._status[account_id] is AccountStatus.TRADING_RESTRICTED
                ):
                    # It couldn't be sold before the close either (owner decision 2026-11-20).
                    reason = ExpiryReason.NOT_EXERCISABLE
                else:
                    self._exercise(action.at, account, key, owned, close)
                    continue
                self._expire(action.at, account_id, option_id, owned, day, close, reason)

    def _exercise(
        self,
        at: datetime,
        account: str,
        option: str,
        contracts: Decimal,
        expiry_close: Decimal | None,
    ) -> None:
        """Exercise ``contracts`` (early if ``expiry_close`` is None, else at expiry)."""
        day = business_date(at)
        if (shortfall := self.exercise_shortfall(account, option, contracts, day)) is not None:
            msg = f"{account} can't exercise {contracts} {option}: {shortfall}"
            raise ScenarioError(msg)
        account_id, _ = self._account(account)
        option_id, spec = self._instrument(option)
        if spec.option is None:  # pragma: no cover - checked by exercise_shortfall
            raise ScenarioError(option)
        underlying_id, underlying = self._instrument(spec.option.underlying)
        settles_on = settlement_date(
            day,
            underlying.market,
            underlying.asset_class,
            self._p.settlement_rules,
            self._p.calendars[underlying.market],
        )
        exercise_id = self._next_id(EntityType.EXERCISE)
        shares = contracts * spec.multiplier
        payload = OptionExercisedPayload(
            exercise_id=exercise_id,
            settlement_id=self._next_id(EntityType.SETTLEMENT),
            account_id=account_id,
            option_instrument_id=option_id,
            underlying_instrument_id=underlying_id,
            right=spec.option.right,
            contracts=contracts,
            multiplier=spec.multiplier,
            shares=shares,
            strike=spec.option.strike,
            strike_amount=spec.option.strike * shares,
            currency=spec.currency,
            exercise_date=day,
            settlement_date=settles_on,
            reason=ExerciseReason.EARLY if expiry_close is None else ExerciseReason.EXPIRY,
            underlying_close=expiry_close,
            expiry_policy_version=None if expiry_close is None else self._p.option_expiry.version,
        )
        self._record(
            _Draft(at, exercise_id, None, _maker(OptionExercised, at, account_id, payload)),
            OptionExercised(**_envelope(at, account_id), payload=payload),
        )
        settles_at = _local(settles_on, self._settlement_time)
        self._exercises[exercise_id] = _Exercise(exercise_id, payload, settles_at)
        self._queued += 1
        heapq.heappush(
            self._corporate_queue, (settles_at, self._queued, "settle_exercise", exercise_id)
        )

    def _expire(
        self,
        at: datetime,
        account_id: str,
        option_id: str,
        contracts: Decimal,
        day: date,
        close: Decimal,
        reason: ExpiryReason,
    ) -> None:
        payload = OptionExpiredPayload(
            account_id=account_id,
            instrument_id=option_id,
            contracts=contracts,
            expiry_date=day,
            underlying_close=close,
            expiry_policy_version=self._p.option_expiry.version,
            reason=reason,
        )
        ref = f"exp:{account_id}:{option_id}"
        self._record(
            _Draft(at, ref, None, _maker(OptionExpired, at, account_id, payload)),
            OptionExpired(**_envelope(at, account_id), payload=payload),
        )

    def _settle_exercise(self, exercise_id: str) -> None:
        exercise = self._exercises.pop(exercise_id)
        p, at = exercise.payload, exercise.settles_at
        payload = ExerciseSettledPayload(
            settlement_id=p.settlement_id,
            exercise_id=exercise_id,
            account_id=p.account_id,
            instrument_id=p.underlying_instrument_id,
            side=Side.BUY if p.right is OptionRight.CALL else Side.SELL,
            quantity=p.shares,
            amount=p.strike_amount,
            currency=p.currency,
            settlement_date=p.settlement_date,
        )
        self._record(
            _Draft(
                at,
                p.settlement_id,
                exercise.ref,
                _maker(ExerciseSettled, at, p.account_id, payload),
            ),
            ExerciseSettled(**_envelope(at, p.account_id), payload=payload),
        )

    def _close(self, action: ClosePrice) -> None:
        self._closes[(action.instrument, action.price_date)] = action.close
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
