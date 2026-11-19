"""Scripted scenarios: one account, and exactly what happens to it.

A scenario file lists the account, its deposits, withdrawals, conversions, and
orders with their fills. ``generate_events`` turns it into engine actions and
runs them through ``SimulationEngine``, which applies policy (fees,
settlement, FX quotes, interest) and refuses anything a brokerage would
refuse. Scenarios are used for golden tests and the demo, for example the
Sprint 1 slice and the Phase 2 flagship (charter §4, steps 1-5).
"""

from datetime import date, time
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from northstar.common.money import Currency, Money
from northstar.domain.events import (
    AccountType,
    Event,
    FxRate,
    OrderType,
    PositiveAmount,
    Price,
    Quantity,
    Side,
    UtcTimestamp,
)
from northstar.simulation.engine import (
    Action,
    Convert,
    Deposit,
    Fill,
    InstrumentSpec,
    OpenAccount,
    PlaceOrder,
    Policies,
    SimulationEngine,
    Withdraw,
)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ScenarioAccount(_Frozen):
    """The client account the scenario opens."""

    account_type: AccountType
    base_currency: Currency
    opened_at: UtcTimestamp


class ScenarioCash(_Frozen):
    """A deposit or withdrawal."""

    at: UtcTimestamp
    amount: PositiveAmount
    currency: Currency


class ScenarioConversion(_Frozen):
    """A client FX conversion of ``sell_amount`` into the other currency."""

    at: UtcTimestamp
    sell_amount: PositiveAmount
    sell_currency: Currency


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
    cancel_remainder_at: UtcTimestamp | None = None

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


class ScenarioRate(_Frozen):
    """A synthetic reference USD/CAD rate (CAD per USD) published for a date."""

    date: date
    rate: FxRate


class Scenario(_Frozen):
    """A complete, scripted simulation scenario. Its values are illustrative.

    ``settlement_time_local`` is the America/Toronto time at which settlements
    are stamped. Interest accrues through ``interest_until``, which defaults
    to the date of the last action.

    Raises:
        pydantic.ValidationError: Instrument keys repeat, or an order names an
            unknown instrument.
    """

    scenario_id: str
    description: str
    illustrative: bool
    settlement_time_local: time
    account: ScenarioAccount
    instruments: tuple[InstrumentSpec, ...]
    fx_rates: tuple[ScenarioRate, ...] = ()
    deposits: tuple[ScenarioCash, ...] = ()
    withdrawals: tuple[ScenarioCash, ...] = ()
    conversions: tuple[ScenarioConversion, ...] = ()
    orders: tuple[ScenarioOrder, ...] = ()
    interest_until: date | None = None

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


ACCOUNT_KEY = "client"


def scenario_actions(scenario: Scenario) -> list[Action]:
    """Return the scenario's actions in time order (ties: open, cash, FX, orders)."""
    actions: list[Action] = [
        OpenAccount(
            at=scenario.account.opened_at,
            account=ACCOUNT_KEY,
            account_type=scenario.account.account_type,
            base_currency=scenario.account.base_currency,
        )
    ]
    actions += [Deposit(d.at, ACCOUNT_KEY, Money(d.amount, d.currency)) for d in scenario.deposits]
    actions += [
        Withdraw(w.at, ACCOUNT_KEY, Money(w.amount, w.currency)) for w in scenario.withdrawals
    ]
    actions += [
        Convert(c.at, ACCOUNT_KEY, Money(c.sell_amount, c.sell_currency))
        for c in scenario.conversions
    ]
    actions += [
        PlaceOrder(
            at=o.at,
            account=ACCOUNT_KEY,
            instrument=o.instrument,
            side=o.side,
            order_type=o.order_type,
            quantity=o.quantity,
            limit_price=o.limit_price,
            fills=tuple(Fill(f.at, f.quantity, f.price) for f in o.fills),
            cancel_remainder_at=o.cancel_remainder_at,
        )
        for o in scenario.orders
    ]
    return sorted(actions, key=lambda a: a.at)  # stable: the order above breaks ties


def generate_events(scenario: Scenario, seed: int, policies: Policies) -> tuple[Event, ...]:
    """Run the scenario through the engine and return its canonical events.

    Raises:
        ScenarioError: The brokerage would refuse an action (an overdraft, a
            short sale, an FX conversion with no rate, ...).
        NotATradingDayError: A fill is not on a trading day.
        PolicyNotFoundError, CalendarCoverageError: Policy or calendar gaps.
    """
    engine = SimulationEngine(
        policies,
        scenario.instruments,
        {r.date: r.rate for r in scenario.fx_rates},
        seed,
        scenario.settlement_time_local,
    )
    actions = scenario_actions(scenario)
    for action in actions:
        engine.submit(action)
    last = max(
        [a.at for a in actions]
        + [f.at for a in actions if isinstance(a, PlaceOrder) for f in a.fills]
    )
    return engine.finish(scenario.interest_until or last.date())
