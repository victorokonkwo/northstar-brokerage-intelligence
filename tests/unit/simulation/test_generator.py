"""The seeded generator: determinism, coverage, invariants, and FR-SIM-05 patterns."""

from collections import Counter
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from northstar.common.exceptions import ScenarioError
from northstar.domain.calendar import BUSINESS_TIMEZONE, business_date
from northstar.domain.events import (
    AccountCreated,
    AccountStatus,
    AccountType,
    AccountUpdated,
    AccountUpdateReason,
    Event,
    EventType,
    ExerciseReason,
    ExpiryReason,
    InstrumentListed,
    OptionExercised,
    OptionExpired,
    OrderPlaced,
    Side,
    TradeExecuted,
)
from northstar.domain.positions import PositionBook
from northstar.infrastructure.config_loader import load_policy
from northstar.infrastructure.event_files import write_events
from northstar.simulation.generator import (
    DatasetConfig,
    build_market,
    generate_dataset,
    market_stream,
)

from factories import CONFIG, engine, policies

DEV = CONFIG / "simulation" / "datasets" / "dev.yaml"


@pytest.fixture(scope="module")
def config() -> DatasetConfig:
    return load_policy(DEV, DatasetConfig)


@pytest.fixture(scope="module")
def dataset(config: DatasetConfig) -> tuple[Event, ...]:
    return generate_dataset(config, 42, policies())


def test_same_seed_gives_byte_identical_files(config: DatasetConfig, tmp_path: Path) -> None:
    write_events(tmp_path / "a.jsonl", generate_dataset(config, 42, policies()))
    write_events(tmp_path / "b.jsonl", generate_dataset(config, 42, policies()))
    assert (tmp_path / "a.jsonl").read_bytes() == (tmp_path / "b.jsonl").read_bytes()


def test_another_seed_gives_another_dataset(
    config: DatasetConfig, dataset: tuple[Event, ...]
) -> None:
    other = generate_dataset(config, 43, policies())
    assert [e.event_type for e in other] != [e.event_type for e in dataset]


def test_every_event_type_and_behaviour_occurs(dataset: tuple[Event, ...]) -> None:
    assert set(Counter(e.event_type for e in dataset)) == set(EventType)
    fills = [e.payload for e in dataset if isinstance(e, TradeExecuted)]
    per_order = Counter(f.order_id for f in fills)
    assert any(n > 1 for n in per_order.values()), "partial fills"
    assert {f.side.value for f in fills} == {"BUY", "SELL"}
    assert {f.currency.value for f in fills} == {"CAD", "USD"}
    assert {f.asset_class.value for f in fills} == {"EQUITY", "OPTION"}
    assert sum(1 for f in fills if f.clearing_fee > 0) > 0


def test_dataset_ledger_balances_and_positions_are_consistent(dataset: tuple[Event, ...]) -> None:
    for batch in engine().post_events(dataset):
        for debits, credits in batch.totals().values():
            assert debits == credits  # INV-01
    book = PositionBook().apply_all(dataset)  # raises on a short sale or over-settlement
    assert all(line.balanced for line in book.stock_record())  # INV-04
    assert all(td >= 0 for td, _ in book.positions().values())  # long only
    # Every obligation settled by the end, so trade-date and settled positions agree.
    assert all(td == settled for td, settled in book.positions().values())


@pytest.fixture(scope="module")
def big(config: DatasetConfig) -> tuple[Event, ...]:
    """400 accounts, so statistical patterns stand out from noise (the dev dataset
    has only about 14 withdrawals: too few to test their timing)."""
    accounts = config.accounts.model_copy(update={"count": 400})
    return generate_dataset(config.model_copy(update={"accounts": accounts}), 1, policies())


def test_month_end_withdrawals_are_more_frequent(
    config: DatasetConfig, big: tuple[Event, ...]
) -> None:
    ledger = policies().calendars["XTSE"]
    days = [
        date.fromordinal(n)
        for n in range(config.window.start.toordinal(), config.window.end.toordinal() + 1)
    ]
    business = [d for d in days if ledger.is_settlement_day(d)]
    month_end = {
        d
        for month in {(d.year, d.month) for d in business}
        for d in [b for b in business if (b.year, b.month) == month][
            -config.withdrawals.month_end_days :
        ]
    }
    withdrawals = [
        business_date(e.event_timestamp) for e in big if e.event_type is EventType.CASH_WITHDRAWN
    ]
    assert len(withdrawals) > 50
    share_of_withdrawals = Decimal(sum(d in month_end for d in withdrawals)) / len(withdrawals)
    share_of_days = Decimal(len(month_end)) / len(business)
    assert share_of_withdrawals > 2 * share_of_days


def test_order_activity_rises_with_price_moves(
    config: DatasetConfig, dataset: tuple[Event, ...]
) -> None:
    market = build_market(
        config, policies().calendars, policies().risk_free_rates, market_stream(42)
    )
    orders = Counter(
        business_date(e.event_timestamp) for e in dataset if e.event_type is EventType.ORDER_PLACED
    )
    days = sorted(market.activity)
    correlation = np.corrcoef(
        [float(market.activity[d]) for d in days], [orders.get(d, 0) for d in days]
    )[0, 1]
    assert correlation > 0.3


def test_deposits_follow_the_seasonal_factors(big: tuple[Event, ...]) -> None:
    # February (factor 1.4) beats March (1.0) per business day, excluding each
    # account's opening deposit.
    events = big
    ledger = policies().calendars["XTSE"]
    seen: set[str] = set()
    per_month: Counter[int] = Counter()
    for e in events:
        if e.event_type is EventType.CASH_DEPOSITED:
            account = e.payload.account_id
            if account in seen:
                per_month[business_date(e.event_timestamp).month] += 1
            seen.add(account)
    business = Counter(
        d.month
        for d in (
            date.fromordinal(n)
            for n in range(date(2026, 2, 1).toordinal(), date(2026, 4, 1).toordinal())
        )
        if ledger.is_settlement_day(d)
    )
    assert per_month[2] / business[2] > Decimal("1.2") * Decimal(per_month[3]) / business[3]


class TestConfigValidation:
    def test_a_missing_seasonal_month_is_rejected(self, config: DatasetConfig) -> None:
        raw = config.model_dump(mode="json")
        raw["deposits"]["seasonal"] = {"1": "1.0", "2": "1.0"}
        with pytest.raises(ValidationError, match=r"missing for months \[3\]"):
            DatasetConfig.model_validate(raw)

    def test_an_inverted_budget_range_is_rejected(self, config: DatasetConfig) -> None:
        raw = config.model_dump(mode="json")
        raw["orders"]["budget_min"] = "0.5"
        with pytest.raises(ValidationError, match="budget_min exceeds budget_max"):
            DatasetConfig.model_validate(raw)

    def test_an_empty_clock_step_range_is_rejected(self, config: DatasetConfig) -> None:
        raw = config.model_dump(mode="json")
        raw["clock_step"] = {"unit": "seconds", "min": 90, "max": 90}
        with pytest.raises(ValidationError, match=r"clock_step\.min must be below"):
            DatasetConfig.model_validate(raw)


class TestClockStep:
    def test_a_day_that_runs_past_the_close_publication_is_refused(
        self, config: DatasetConfig
    ) -> None:
        # Many accounts with 5-90 second steps overrun 16:30, as the load test found.
        crowded = config.model_copy(
            update={"accounts": config.accounts.model_copy(update={"count": 2000})}
        )
        with pytest.raises(ScenarioError, match="past the 16:30:00 close publication"):
            generate_dataset(crowded, 42, policies())

    def test_millisecond_steps_keep_a_crowded_day_inside_the_session(
        self, config: DatasetConfig
    ) -> None:
        fast = config.model_copy(
            update={
                "accounts": config.accounts.model_copy(update={"count": 2000}),
                "clock_step": config.clock_step.model_copy(update={"unit": "milliseconds"}),
            }
        )
        events = generate_dataset(fast, 42, policies())
        assert sum(1 for e in events if isinstance(e, TradeExecuted)) > 0


class TestAccountLifecycle:
    def test_approvals_and_restrictions_occur(self, dataset: tuple[Event, ...]) -> None:
        reasons = {e.payload.reason for e in dataset if isinstance(e, AccountUpdated)}
        assert {
            AccountUpdateReason.MARGIN_APPROVED,
            AccountUpdateReason.TRADING_RESTRICTED,
        } <= reasons

    def test_a_restriction_lasts_the_configured_business_days(self, config: DatasetConfig) -> None:
        always = config.model_copy(
            update={
                "lifecycle": config.lifecycle.model_copy(
                    update={"restriction_probability": Decimal(1)}
                )
            }
        )
        events = generate_dataset(always, 42, policies())
        ledger = policies().calendars["XTSE"]
        starts: dict[str, date] = {}
        lifted = 0
        for e in events:
            if not isinstance(e, AccountUpdated):
                continue
            day = business_date(e.event_timestamp)
            if e.payload.reason is AccountUpdateReason.TRADING_RESTRICTED:
                starts[e.payload.account_id] = day
            elif e.payload.reason is AccountUpdateReason.RESTRICTION_LIFTED:
                start = starts.pop(e.payload.account_id)
                assert (
                    ledger.add_settlement_days(start, always.lifecycle.restriction_business_days)
                    == day
                )
                lifted += 1
        assert lifted > 0
        assert len({e.payload.account_id for e in events if isinstance(e, AccountCreated)}) == 50

    def test_accounts_trade_only_while_active_and_borrow_only_while_margin(
        self, dataset: tuple[Event, ...]
    ) -> None:
        # Replay each account's attributes from its events and check every order.
        state: dict[str, tuple[AccountType, AccountStatus]] = {}
        for e in dataset:
            if isinstance(e, AccountCreated):
                state[e.payload.account_id] = (e.payload.account_type, AccountStatus.ACTIVE)
            elif isinstance(e, AccountUpdated):
                assert state[e.payload.account_id] != (e.payload.account_type, e.payload.status)
                state[e.payload.account_id] = (e.payload.account_type, e.payload.status)
            elif isinstance(e, OrderPlaced):
                assert state[e.payload.account_id][1] is AccountStatus.ACTIVE
        cash_after_approval = {
            e.payload.account_id
            for e in dataset
            if isinstance(e, AccountUpdated)
            and e.payload.reason is AccountUpdateReason.MARGIN_APPROVED
        }
        created_cash = {
            e.payload.account_id
            for e in dataset
            if isinstance(e, AccountCreated) and e.payload.account_type is AccountType.CASH
        }
        assert cash_after_approval <= created_cash

    def test_an_update_changes_only_what_its_reason_names(self, config: DatasetConfig) -> None:
        # Every account is approved and restricted, so approvals land inside restrictions.
        busy = config.model_copy(
            update={
                "lifecycle": config.lifecycle.model_copy(
                    update={
                        "margin_approval_probability": Decimal(1),
                        "restriction_probability": Decimal(1),
                        "restriction_business_days": 20,
                    }
                )
            }
        )
        state: dict[str, tuple[AccountType, AccountStatus]] = {}
        approvals_while_restricted = 0
        for e in generate_dataset(busy, 42, policies()):
            if isinstance(e, AccountCreated):
                state[e.payload.account_id] = (e.payload.account_type, AccountStatus.ACTIVE)
            elif isinstance(e, AccountUpdated):
                before_type, before_status = state[e.payload.account_id]
                if e.payload.reason is AccountUpdateReason.MARGIN_APPROVED:
                    assert e.payload.status is before_status
                    approvals_while_restricted += before_status is AccountStatus.TRADING_RESTRICTED
                else:
                    assert e.payload.account_type is before_type
                state[e.payload.account_id] = (e.payload.account_type, e.payload.status)
        assert approvals_while_restricted > 0


def test_in_the_money_options_are_exercised_or_closed_out_at_expiry(
    config: DatasetConfig,
) -> None:
    """The expiry policy (ADR 013), on a dataset where many puts can't be exercised:
    the SYNG put struck at 40.00 is deep in the money, and few holders own the shares."""
    deep = tuple(
        i.model_copy(update={"option": i.option.model_copy(update={"strike": Decimal("40.00")})})
        if i.key == "SYNG-P" and i.option is not None
        else i
        for i in config.instruments
    )
    events = generate_dataset(config.model_copy(update={"instruments": deep}), 42, policies())
    expiry = date(2026, 3, 20)
    put = next(
        e.payload.instrument_id
        for e in events
        if isinstance(e, InstrumentListed) and e.payload.symbol == "SYNG-P-20260320-22.5"
    )
    before = PositionBook().apply_all(
        e for e in events if e.event_timestamp < datetime(2026, 3, 20, 20, 0, tzinfo=UTC)
    )  # holdings before the 16:00 Toronto close-outs
    held = {a: q for (a, i), q in before.positions().items() if i == put and q[0] > 0}
    closed_out = {
        e.payload.account_id
        for e in events
        if isinstance(e, TradeExecuted)
        and e.payload.instrument_id == put
        and e.payload.side is Side.SELL
        and e.payload.trade_date == expiry
        and e.event_timestamp.astimezone(BUSINESS_TIMEZONE).time() >= time(16, 0)
    }
    exercised = {
        e.payload.account_id
        for e in events
        if isinstance(e, OptionExercised)
        and e.payload.option_instrument_id == put
        and e.payload.exercise_date == expiry  # deep puts are also exercised early
    }
    unexercisable = {
        e.payload.account_id
        for e in events
        if isinstance(e, OptionExpired)
        and e.payload.instrument_id == put
        and e.payload.reason is ExpiryReason.NOT_EXERCISABLE
    }
    assert closed_out  # most holders don't own 100 SYNG per contract
    assert set(held) == closed_out | exercised | unexercisable
    assert not closed_out & exercised
    # Sold at the option's close that day: its intrinsic value at expiry.
    book = PositionBook().apply_all(events)
    assert not [k for k in book.positions() if k[1] == put]


def test_early_exercises_happen_only_when_the_model_says(
    config: DatasetConfig, dataset: tuple[Event, ...]
) -> None:
    market = build_market(
        config, policies().calendars, policies().risk_free_rates, market_stream(42)
    )
    keys = {
        e.payload.instrument_id: next(
            i.key for i in config.instruments if i.symbol == e.payload.symbol
        )
        for e in dataset
        if isinstance(e, InstrumentListed)
    }
    early = [
        (keys[e.payload.option_instrument_id], e.payload.exercise_date)
        for e in dataset
        if isinstance(e, OptionExercised) and e.payload.reason is ExerciseReason.EARLY
    ]
    assert early
    for key, day in early:
        assert day in market.exercise_optimal[key], (key, day)


def test_nobody_buys_an_option_on_or_after_its_expiry_day(config: DatasetConfig) -> None:
    # Every order an option order, so buys are attempted on expiry days too. A buy
    # on the expiry day couldn't settle before the expiry processing.
    options_only = config.model_copy(
        update={"orders": config.orders.model_copy(update={"option_probability": Decimal(1)})}
    )
    events = generate_dataset(options_only, 42, policies())
    expiry = {
        e.payload.instrument_id: e.payload.option.expiry
        for e in events
        if isinstance(e, InstrumentListed) and e.payload.option is not None
    }
    bought = [
        e.payload
        for e in events
        if isinstance(e, TradeExecuted)
        and e.payload.side is Side.BUY
        and e.payload.instrument_id in expiry
    ]
    assert bought  # plenty of option buys, up to the day before each expiry
    assert not [p for p in bought if p.trade_date >= expiry[p.instrument_id]]


def test_activity_is_the_average_move_of_the_equities(config: DatasetConfig) -> None:
    # Options echo their underlyings' moves, levered, so they don't count (FR-SIM-05).
    market = build_market(
        config, policies().calendars, policies().risk_free_rates, market_stream(42)
    )
    equities = [i.key for i in config.instruments if i.option is None]
    for day in (date(2026, 2, 20), date(2026, 3, 20)):  # option expiry days
        moves = [
            abs(market.closes[k][day] / market.closes[k][prev] - 1)
            for k in equities
            if day in market.closes[k]
            for prev in [max(d for d in market.closes[k] if d < day)]
        ]
        assert market.activity[day] == sum(moves, Decimal(0)) / len(moves)
