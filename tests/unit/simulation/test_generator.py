"""The seeded generator: determinism, coverage, invariants, and FR-SIM-05 patterns."""

from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from northstar.domain.calendar import business_date
from northstar.domain.events import Event, EventType, TradeExecuted
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


def test_month_end_withdrawals_are_more_frequent(
    config: DatasetConfig, dataset: tuple[Event, ...]
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
        business_date(e.event_timestamp)
        for e in dataset
        if e.event_type is EventType.CASH_WITHDRAWN
    ]
    share_of_withdrawals = Decimal(sum(d in month_end for d in withdrawals)) / len(withdrawals)
    share_of_days = Decimal(len(month_end)) / len(business)
    assert share_of_withdrawals > 2 * share_of_days


def test_order_activity_rises_with_price_moves(
    config: DatasetConfig, dataset: tuple[Event, ...]
) -> None:
    market = build_market(config, policies().calendars, market_stream(42))
    orders = Counter(
        business_date(e.event_timestamp) for e in dataset if e.event_type is EventType.ORDER_PLACED
    )
    days = sorted(market.activity)
    correlation = np.corrcoef(
        [float(market.activity[d]) for d in days], [orders.get(d, 0) for d in days]
    )[0, 1]
    assert correlation > 0.3


def test_deposits_follow_the_seasonal_factors(config: DatasetConfig) -> None:
    # 400 accounts so the pattern stands out from noise. February (factor 1.4)
    # beats March (1.0) per business day, excluding each account's opening deposit.
    big = config.model_copy(update={"accounts": config.accounts.model_copy(update={"count": 400})})
    events = generate_dataset(big, 1, policies())
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
