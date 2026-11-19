"""Seeded random brokerage generator (FR-SIM-01, FR-SIM-05).

A dataset file sets the window, the account mix, a synthetic instrument
universe, and activity parameters. ``generate_dataset`` drives the
``SimulationEngine`` day by day:

- **Market data:** synthetic closes per instrument trading day, and a
  synthetic USD/CAD reference rate per ledger business day (``market.py``).
- **Accounts** open during the first ten business days with an initial
  deposit. A configured share are MARGIN accounts. Later, some CASH accounts
  are approved for margin, and some accounts are restricted from trading for
  a few business days (``account_updated``, the SCD2 history of FR-M1-05).
  Updates are processed at 08:00 local, before the market opens.
- **Treasury patterns (FR-SIM-05):** deposit probability scales by month
  (seasonality); withdrawal probability rises over the last business days of
  each month; order intensity rises with the day's average absolute price
  move (volatility-driven activity).
- **Orders:** buys sized as a random fraction of buying power (cash, plus part
  of the remaining loan headroom for MARGIN accounts), and sells of part of an
  existing position. Each has 1-3 partial fills near the last close, and is
  sometimes cancelled after a partial fill. Buying a USD instrument without
  enough USD first converts CAD.

Determinism: one ``numpy.random.SeedSequence(seed)`` is split into a market
stream and one stream per account. Each account's lifecycle plan is drawn
from a child of its stream, so planning consumes none of its activity draws.
Every arithmetic step after the random draw is ``Decimal``. Actions are
generated in one global time order per day, so the same seed and config
always give byte-identical events.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import Annotated

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from northstar.common.money import Currency, Money
from northstar.domain.calendar import BUSINESS_TIMEZONE, MarketCalendar
from northstar.domain.events import (
    AccountStatus,
    AccountType,
    AccountUpdateReason,
    Event,
    OrderType,
    Side,
)
from northstar.domain.settlement import AssetClass
from northstar.simulation.engine import (
    LEDGER_CALENDAR_MARKET,
    ClosePrice,
    Convert,
    Deposit,
    Fill,
    InstrumentSpec,
    OpenAccount,
    PlaceOrder,
    Policies,
    SimulationEngine,
    UpdateAccount,
    Withdraw,
)
from northstar.simulation.market import PRICE_TICK, RATE_PLACES, absolute_returns, log_normal_path

Probability = Annotated[Decimal, Field(ge=0, le=1)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Window(_Frozen):
    """The simulated period (inclusive)."""

    start: date
    end: date


class AccountMix(_Frozen):
    """How many accounts, and the share that are MARGIN accounts."""

    count: Annotated[int, Field(ge=1)]
    margin_share: Probability
    base_currency: Currency


class GeneratedInstrument(InstrumentSpec):
    """An instrument plus the parameters of its synthetic price path."""

    start_price: Decimal = Field(gt=0)
    daily_vol: Decimal = Field(gt=0, lt=1)


class FxPath(_Frozen):
    """The synthetic USD/CAD reference-rate path (CAD per USD)."""

    start_rate: Decimal = Field(gt=0)
    daily_vol: Decimal = Field(gt=0, lt=1)


class AccountLifecycle(_Frozen):
    """Account changes after opening (each probability is per account, over the window)."""

    margin_approval_probability: Probability
    """Chance that a CASH account is approved for margin on a later business day."""
    restriction_probability: Probability
    """Chance that an account is restricted from trading on a later business day."""
    restriction_business_days: Annotated[int, Field(ge=1)]
    """Ledger business days until the restriction is lifted (if within the window)."""


class DepositActivity(_Frozen):
    """Deposits: a daily probability, scaled by a monthly seasonal factor."""

    daily_probability: Probability
    seasonal: dict[int, Decimal]
    mean_amount: Decimal = Field(gt=0)
    initial_mean_amount: Decimal = Field(gt=0)
    min_amount: Decimal = Field(gt=0)


class WithdrawalActivity(_Frozen):
    """Withdrawals: more likely over the last business days of a month."""

    daily_probability: Probability
    month_end_multiplier: Decimal = Field(ge=1)
    month_end_days: Annotated[int, Field(ge=1)]
    max_fraction: Probability


class OrderActivity(_Frozen):
    """Order intensity and sizing."""

    daily_rate: Decimal = Field(gt=0)
    volatility_sensitivity: Decimal = Field(ge=0)
    sell_probability: Probability
    option_probability: Probability
    budget_min: Probability
    budget_max: Probability
    margin_headroom_use: Probability
    usd_conversion_fraction: Probability
    max_partial_fills: Annotated[int, Field(ge=1, le=5)]
    cancel_probability: Probability
    price_noise: Decimal = Field(ge=0, lt=Decimal("0.1"))


class DatasetConfig(_Frozen):
    """A generator configuration. Every value is illustrative.

    Raises:
        pydantic.ValidationError: The window is empty, a seasonal month is
            missing, or the budget range is inverted.
    """

    dataset_id: str
    illustrative: bool
    window: Window
    settlement_time_local: time
    accounts: AccountMix
    lifecycle: AccountLifecycle
    fx: FxPath
    instruments: tuple[GeneratedInstrument, ...]
    deposits: DepositActivity
    withdrawals: WithdrawalActivity
    orders: OrderActivity

    @model_validator(mode="after")
    def _consistent(self) -> "DatasetConfig":
        if self.window.start > self.window.end:
            msg = "the window start is after its end"
            raise ValueError(msg)
        months = {d.month for d in _days(self.window.start, self.window.end)}
        if missing := months - set(self.deposits.seasonal):
            msg = f"seasonal deposit factors are missing for months {sorted(missing)}"
            raise ValueError(msg)
        if self.orders.budget_min > self.orders.budget_max:
            msg = "budget_min exceeds budget_max"
            raise ValueError(msg)
        return self


@dataclass(frozen=True, slots=True)
class MarketData:
    """The synthetic market a dataset runs on: closes, reference rates, and daily activity.

    ``activity`` is the average absolute close-to-close move across instruments
    per day, which scales order intensity (FR-SIM-05).
    """

    closes: dict[str, dict[date, Decimal]]
    rates: dict[date, Decimal]
    activity: dict[date, Decimal]
    mean_activity: Decimal


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def _local(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=BUSINESS_TIMEZONE).astimezone(UTC)


def _uniform(rng: np.random.Generator) -> Decimal:
    return Decimal(float(rng.random()))


def build_market(
    config: DatasetConfig, calendars: dict[str, MarketCalendar], rng: np.random.Generator
) -> MarketData:
    """Build the synthetic market from the market random stream (the dataset seed's first child)."""
    days = _days(config.window.start, config.window.end)
    closes = {}
    for spec in config.instruments:
        calendar = calendars[spec.market]
        trading = [d for d in days if calendar.is_trading_day(d)]
        closes[spec.key] = log_normal_path(
            rng, spec.start_price, spec.daily_vol, trading, PRICE_TICK
        )
    ledger = calendars[LEDGER_CALENDAR_MARKET]
    # The reference rate needs a value at or before the first day.
    rate_days = [config.window.start - timedelta(days=1)] + [
        d for d in days if ledger.is_settlement_day(d)
    ]
    rates = log_normal_path(rng, config.fx.start_rate, config.fx.daily_vol, rate_days, RATE_PLACES)
    moves: dict[date, list[Decimal]] = {}
    for path in closes.values():
        for day, move in absolute_returns(path).items():
            moves.setdefault(day, []).append(move)
    activity = {day: sum(m, Decimal(0)) / len(m) for day, m in moves.items()}
    mean = sum(activity.values(), Decimal(0)) / len(activity) if activity else Decimal(1)
    return MarketData(closes, rates, activity, mean or Decimal(1))


def _month_end_days(config: DatasetConfig, ledger: MarketCalendar) -> set[date]:
    business = [
        d for d in _days(config.window.start, config.window.end) if ledger.is_settlement_day(d)
    ]
    by_month: dict[tuple[int, int], list[date]] = {}
    for day in business:
        by_month.setdefault((day.year, day.month), []).append(day)
    return {d for days in by_month.values() for d in days[-config.withdrawals.month_end_days :]}


_BACK_OFFICE_TIME = time(8, 0)
"""When account updates are processed (simulation convention, before the open)."""


class _Clock:
    """One global time order for the day's actions (the engine requires it)."""

    def __init__(self, day: date) -> None:
        self.now = _local(day, time(9, 35))
        self.close = _local(day, time(15, 55))

    def step(self, rng: np.random.Generator) -> datetime:
        self.now += timedelta(seconds=int(rng.integers(5, 90)))
        return self.now

    @property
    def open(self) -> bool:
        return self.now < self.close


def market_stream(seed: int) -> np.random.Generator:
    """The market's random stream: child 0 of ``SeedSequence(seed)``, whatever the account count."""
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence(seed).spawn(1)[0]))


def generate_dataset(config: DatasetConfig, seed: int, policies: Policies) -> tuple[Event, ...]:
    """Generate a dataset's canonical events.

    Raises:
        ScenarioError: The generator produced an action the brokerage refuses
            (a generator bug, since it sizes actions from the engine's own
            balances).
        CalendarCoverageError, PolicyNotFoundError: Calendar or policy gaps.
    """
    streams = np.random.SeedSequence(seed).spawn(1 + config.accounts.count)
    market_rng = market_stream(seed)
    rngs = [np.random.Generator(np.random.PCG64(s)) for s in streams[1:]]
    market = build_market(config, policies.calendars, market_rng)
    instruments = tuple(
        InstrumentSpec.model_validate(i.model_dump(exclude={"start_price", "daily_vol"}))
        for i in config.instruments
    )
    engine = SimulationEngine(
        policies, instruments, market.rates, seed, config.settlement_time_local
    )
    ledger = policies.calendars[LEDGER_CALENDAR_MARKET]
    month_ends = _month_end_days(config, ledger)
    business_days = [
        d for d in _days(config.window.start, config.window.end) if ledger.is_settlement_day(d)
    ]
    accounts = []
    changes: dict[date, list[tuple[str, _Change]]] = {}
    for index, (rng, sequence) in enumerate(zip(rngs, streams[1:], strict=True)):
        key = f"a{index:04d}"
        is_margin = _uniform(rng) < config.accounts.margin_share
        opens_on = business_days[int(rng.integers(0, min(10, len(business_days))))]
        account_type = AccountType.MARGIN if is_margin else AccountType.CASH
        life = np.random.Generator(np.random.PCG64(sequence.spawn(1)[0]))
        for change_day, change in _lifecycle(config, account_type, opens_on, business_days, life):
            changes.setdefault(change_day, []).append((key, change))
        accounts.append((key, account_type, opens_on, rng))

    for day in _days(config.window.start, config.window.end):
        # Back-office updates first, one second apart, without moving the day's clock.
        for n, (key, change) in enumerate(changes.get(day, [])):
            current = (
                AccountStatus.TRADING_RESTRICTED
                if engine.trading_restricted(key)
                else AccountStatus.ACTIVE
            )
            engine.submit(
                UpdateAccount(
                    _local(day, _BACK_OFFICE_TIME) + timedelta(seconds=n),
                    key,
                    change.account_type or engine.account_type(key),
                    change.status or current,
                    change.reason,
                )
            )
        clock = _Clock(day)
        for key, account_type, opens_on, rng in accounts:
            if day < opens_on:
                continue
            if day == opens_on:
                engine.submit(
                    OpenAccount(clock.step(rng), key, account_type, config.accounts.base_currency)
                )
                amount = _amount(
                    rng, config.deposits.initial_mean_amount, config.deposits.min_amount
                )
                engine.submit(Deposit(clock.step(rng), key, Money(amount, Currency.CAD)))
            _day_activity(config, policies, engine, market, key, rng, day, clock, month_ends)
        for spec in config.instruments:  # closes after the market, in config order
            close = market.closes[spec.key].get(day)
            if close is not None:
                engine.submit(ClosePrice(_local(day, time(16, 30)), spec.key, day, close))
    return engine.finish(config.window.end)


@dataclass(frozen=True, slots=True)
class _Change:
    """A planned account update.

    A field left as None keeps its value at that time, so a margin approval
    during a restriction doesn't lift the restriction.
    """

    reason: AccountUpdateReason
    account_type: AccountType | None = None
    status: AccountStatus | None = None


def _lifecycle(
    config: DatasetConfig,
    account_type: AccountType,
    opens_on: date,
    business_days: list[date],
    rng: np.random.Generator,
) -> list[tuple[date, _Change]]:
    """Plan the account's updates from its lifecycle stream (always the same draws)."""
    life = config.lifecycle
    later = [d for d in business_days if d > opens_on]
    approve = _uniform(rng) < life.margin_approval_probability
    approve_index = int(rng.integers(0, len(later))) if later else 0
    restrict = _uniform(rng) < life.restriction_probability
    restrict_index = int(rng.integers(0, len(later))) if later else 0
    planned: list[tuple[date, _Change]] = []
    if not later:
        return planned
    if approve and account_type is AccountType.CASH:
        planned.append(
            (
                later[approve_index],
                _Change(AccountUpdateReason.MARGIN_APPROVED, account_type=AccountType.MARGIN),
            )
        )
    if restrict:
        start = restrict_index
        planned.append(
            (
                later[start],
                _Change(
                    AccountUpdateReason.TRADING_RESTRICTED, status=AccountStatus.TRADING_RESTRICTED
                ),
            )
        )
        lift = start + life.restriction_business_days
        if lift < len(later):
            planned.append(
                (
                    later[lift],
                    _Change(AccountUpdateReason.RESTRICTION_LIFTED, status=AccountStatus.ACTIVE),
                )
            )
    return sorted(planned, key=lambda p: p[0])


def _amount(rng: np.random.Generator, mean: Decimal, minimum: Decimal) -> Decimal:
    """A log-normal amount in whole dollars around ``mean``, at least ``minimum``."""
    z = Decimal(float(rng.standard_normal()))
    value = mean * (Decimal("0.6") * z - Decimal("0.18")).exp()
    return max(value.quantize(Decimal(1), rounding=ROUND_FLOOR), minimum).quantize(Decimal("0.01"))


def _day_activity(
    config: DatasetConfig,
    policies: Policies,
    engine: SimulationEngine,
    market: MarketData,
    key: str,
    rng: np.random.Generator,
    day: date,
    clock: _Clock,
    month_ends: set[date],
) -> None:
    deposits, withdrawals, orders = config.deposits, config.withdrawals, config.orders
    ledger = policies.calendars[LEDGER_CALENDAR_MARKET]
    if ledger.is_settlement_day(day):
        if _uniform(rng) < deposits.daily_probability * deposits.seasonal[day.month]:
            amount = _amount(rng, deposits.mean_amount, deposits.min_amount)
            engine.submit(Deposit(clock.step(rng), key, Money(amount, Currency.CAD)))
        probability = withdrawals.daily_probability * (
            withdrawals.month_end_multiplier if day in month_ends else Decimal(1)
        )
        if _uniform(rng) < probability:
            cash = engine.cash_balance(key, Currency.CAD).amount
            amount = (cash * withdrawals.max_fraction * _uniform(rng)).quantize(
                Decimal(1), rounding=ROUND_FLOOR
            )
            if amount >= 10:
                engine.submit(
                    Withdraw(
                        clock.step(rng), key, Money(amount.quantize(Decimal("0.01")), Currency.CAD)
                    )
                )
    if engine.trading_restricted(key):
        return
    account_type = engine.account_type(key)
    activity = market.activity.get(day, market.mean_activity)
    lam = orders.daily_rate * (
        1 + orders.volatility_sensitivity * (activity / market.mean_activity - 1)
    )
    for _ in range(int(rng.poisson(float(max(lam, Decimal(0)))))):
        if not clock.open:
            break
        _one_order(config, policies, engine, market, key, account_type, rng, day, clock)


def _last_close(market: MarketData, spec: GeneratedInstrument, day: date) -> Decimal:
    closes = market.closes[spec.key]
    prior = [d for d in closes if d < day]
    return closes[max(prior)] if prior else spec.start_price


def _one_order(
    config: DatasetConfig,
    policies: Policies,
    engine: SimulationEngine,
    market: MarketData,
    key: str,
    account_type: AccountType,
    rng: np.random.Generator,
    day: date,
    clock: _Clock,
) -> None:
    orders = config.orders
    want_option = _uniform(rng) < orders.option_probability
    candidates = [
        s for s in config.instruments if (s.asset_class is AssetClass.OPTION) == want_option
    ]
    spec = candidates[int(rng.integers(0, len(candidates)))]
    if market.closes[spec.key].get(day) is None:
        return  # its market is closed today
    reference = _last_close(market, spec, day)
    held = engine.trade_date_quantity(key, spec.key)
    sell = held >= 1 and _uniform(rng) < orders.sell_probability
    if sell:
        quantity = Decimal(int(rng.integers(1, int(held) + 1)))
    else:
        if spec.currency is Currency.USD:
            _ensure_usd(config, engine, key, rng, day, clock)
        quantity = _buy_quantity(
            config, policies, engine, key, account_type, spec, reference, rng, day
        )
        if quantity < 1:
            return
    placed = clock.step(rng)
    fills = _fills(orders, rng, clock, quantity, reference)
    cancel_at = None
    if len(fills) > 1 and _uniform(rng) < orders.cancel_probability:
        fills = fills[:-1]
        cancel_at = clock.step(rng)
    engine.submit(
        PlaceOrder(
            at=placed,
            account=key,
            instrument=spec.key,
            side=Side.SELL if sell else Side.BUY,
            order_type=OrderType.MARKET,
            quantity=quantity,
            fills=fills,
            cancel_remainder_at=cancel_at,
        )
    )


def _ensure_usd(
    config: DatasetConfig,
    engine: SimulationEngine,
    key: str,
    rng: np.random.Generator,
    day: date,
    clock: _Clock,
) -> None:
    usd = engine.cash_balance(key, Currency.USD).amount
    cad = engine.cash_balance(key, Currency.CAD).amount
    if usd > 0 or cad < 100:
        return
    sell = (cad * config.orders.usd_conversion_fraction).quantize(Decimal(1), rounding=ROUND_FLOOR)
    if sell >= 100:
        engine.submit(
            Convert(clock.step(rng), key, Money(sell.quantize(Decimal("0.01")), Currency.CAD))
        )


def _buy_quantity(
    config: DatasetConfig,
    policies: Policies,
    engine: SimulationEngine,
    key: str,
    account_type: AccountType,
    spec: GeneratedInstrument,
    reference: Decimal,
    rng: np.random.Generator,
    day: date,
) -> Decimal:
    orders = config.orders
    cash = max(engine.cash_balance(key, spec.currency).amount, Decimal(0))
    power = cash
    if account_type is AccountType.MARGIN:
        guard = policies.margin_policy.max_debit_balance.amount
        headroom_cad = guard - engine.debit_cad(key, day).amount
        rate = engine.reference_rate(day)
        headroom = headroom_cad if spec.currency is Currency.CAD else headroom_cad / rate
        power += max(headroom, Decimal(0)) * orders.margin_headroom_use
    fraction = orders.budget_min + (orders.budget_max - orders.budget_min) * _uniform(rng)
    unit = reference * spec.multiplier * (1 + orders.price_noise * 4)
    rough = (power * fraction / unit).quantize(Decimal(1), rounding=ROUND_FLOOR)
    if rough < 1:
        return Decimal(0)
    # Leave room for up to max_partial_fills commissions, from the fee schedule.
    fees = policies.fee_schedule.commission(spec.market, spec.currency, day, rough)
    budget = power * fraction - fees.amount * orders.max_partial_fills
    if budget <= 0:
        return Decimal(0)
    return (budget / unit).quantize(Decimal(1), rounding=ROUND_FLOOR)


def _fills(
    orders: OrderActivity,
    rng: np.random.Generator,
    clock: _Clock,
    quantity: Decimal,
    reference: Decimal,
) -> tuple[Fill, ...]:
    count = min(int(rng.integers(1, orders.max_partial_fills + 1)), int(quantity))
    sizes = [Decimal(1)] * count
    for _ in range(int(quantity) - count):
        sizes[int(rng.integers(0, count))] += 1
    fills = []
    for size in sizes:
        at = clock.now + timedelta(seconds=int(rng.integers(1, 10)))
        clock.now = at
        noise = orders.price_noise * Decimal(float(rng.standard_normal()))
        price = max((reference * (1 + noise)).quantize(PRICE_TICK), PRICE_TICK)
        fills.append(Fill(at, size, price))
    return tuple(fills)
