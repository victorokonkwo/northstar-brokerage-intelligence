"""Tests for the scenario-driven simulator, including the committed Sprint 1 scenario."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from northstar.common.exceptions import NotATradingDayError, PolicyNotFoundError, ScenarioError
from northstar.domain.events import (
    AccountCreated,
    CashDeposited,
    EventType,
    InstrumentListed,
    OrderPlaced,
    TradeExecuted,
    TradeSettled,
)
from northstar.infrastructure.config_loader import load_policy
from northstar.infrastructure.event_files import write_events
from northstar.simulation.scenario import Scenario, generate_events

from factories import CONFIG, engine, policies

SPRINT1 = CONFIG / "simulation" / "sprint1_scenario.yaml"


@pytest.fixture(scope="module")
def sprint1() -> Scenario:
    return load_policy(SPRINT1, Scenario)


class TestSprint1Scenario:
    def test_event_sequence(self, sprint1: Scenario) -> None:
        events = generate_events(sprint1, 42, policies())
        assert [e.event_type for e in events] == [
            EventType.INSTRUMENT_LISTED,
            EventType.ACCOUNT_CREATED,
            EventType.CASH_DEPOSITED,
            EventType.ORDER_PLACED,
            EventType.TRADE_EXECUTED,
            EventType.TRADE_EXECUTED,
            EventType.TRADE_SETTLED,
        ]
        assert [e.event_id for e in events] == [f"evt_s42_{n:06d}" for n in range(1, 8)]

    def test_values(self, sprint1: Scenario) -> None:
        listed, account, dep, order, fill_1, fill_2, settled = generate_events(
            sprint1, 42, policies()
        )
        assert isinstance(listed, InstrumentListed)
        assert listed.payload.symbol == "SYNA"
        assert isinstance(account, AccountCreated)
        assert isinstance(dep, CashDeposited)
        assert isinstance(order, OrderPlaced)
        assert isinstance(fill_1, TradeExecuted)
        assert isinstance(fill_2, TradeExecuted)
        assert isinstance(settled, TradeSettled)
        assert dep.payload.amount == Decimal("25000.00")
        for fill in (fill_1, fill_2):
            assert fill.causation_id == order.event_id
            assert fill.payload.commission == Decimal("4.95")  # per execution (OQ-02)
            assert fill.payload.trade_date == date(2026, 3, 3)
            assert fill.payload.settlement_date == date(2026, 3, 4)  # T+1
            assert fill.payload.settlement_id == settled.payload.settlement_id
        assert settled.causation_id == fill_2.event_id
        assert settled.payload.execution_ids == (
            fill_1.payload.execution_id,
            fill_2.payload.execution_id,
        )
        assert settled.payload.amount == Decimal("5238.60")  # 3142.20 + 2096.40
        assert settled.payload.quantity == Decimal(100)
        # 12:00 Toronto (EST) on 2026-03-04 is 17:00 UTC.
        assert settled.event_timestamp == datetime(2026, 3, 4, 17, 0, tzinfo=UTC)

    def test_same_seed_gives_byte_identical_files(self, sprint1: Scenario, tmp_path: Path) -> None:
        first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
        write_events(first, generate_events(sprint1, 42, policies()))
        write_events(second, generate_events(sprint1, 42, policies()))
        assert first.read_bytes() == second.read_bytes()

    def test_seed_changes_ids_only(self, sprint1: Scenario) -> None:
        a, b = generate_events(sprint1, 42, policies()), generate_events(sprint1, 7, policies())
        assert a[0].event_id == "evt_s42_000001"
        assert b[0].event_id == "evt_s7_000001"
        assert [e.event_type for e in a] == [e.event_type for e in b]


def _scenario(**overrides: Any) -> dict[str, Any]:
    raw = load_policy(SPRINT1, Scenario).model_dump(mode="json")  # lists, not tuples
    return raw | overrides


class TestRejectedScenarios:
    def test_fill_on_a_market_holiday(self) -> None:
        raw = _scenario()
        raw["account"] = raw["account"] | {"opened_at": datetime(2026, 2, 2, 14, 0, tzinfo=UTC)}
        raw["deposits"] = [
            d | {"at": datetime(2026, 2, 2, 15, 0, tzinfo=UTC)} for d in raw["deposits"]
        ]
        holiday = datetime(2026, 2, 16, 15, 0, tzinfo=UTC)  # Family Day
        raw["orders"][0] = raw["orders"][0] | {
            "at": holiday,
            "fills": [{"at": holiday, "quantity": "1", "price": "1.00"}],
            "quantity": "1",
        }
        with pytest.raises(NotATradingDayError, match="2026-02-16"):
            generate_events(Scenario.model_validate(raw), 1, policies())

    def test_conversion_without_a_reference_rate(self) -> None:
        # A conversion needs a reference rate; this scenario provides none.
        raw = _scenario() | {
            "conversions": [
                {
                    "at": datetime(2026, 3, 2, 16, 0, tzinfo=UTC),
                    "sell_amount": "100.00",
                    "sell_currency": "CAD",
                }
            ]
        }
        with pytest.raises(PolicyNotFoundError, match="no reference rate"):
            generate_events(Scenario.model_validate(raw), 1, policies())

    def test_market_without_a_calendar(self) -> None:
        raw = _scenario()
        raw["instruments"][0] = raw["instruments"][0] | {"market": "XLON"}
        with pytest.raises(ScenarioError, match="no calendar for market XLON"):
            generate_events(Scenario.model_validate(raw), 1, policies())

    def test_fills_exceeding_the_order(self) -> None:
        raw = _scenario()
        raw["orders"][0] = raw["orders"][0] | {"quantity": "99"}
        with pytest.raises(ValidationError, match="exceed the order quantity"):
            Scenario.model_validate(raw)

    def test_fill_before_the_order(self) -> None:
        raw = _scenario()
        raw["orders"][0] = raw["orders"][0] | {"at": datetime(2026, 3, 3, 16, 0, tzinfo=UTC)}
        with pytest.raises(ValidationError, match="time order"):
            Scenario.model_validate(raw)

    def test_unknown_instrument(self) -> None:
        raw = _scenario()
        raw["orders"][0] = raw["orders"][0] | {"instrument": "NOPE"}
        with pytest.raises(ValidationError, match="unknown instrument"):
            Scenario.model_validate(raw)


def test_sprint1_scenario_posts_the_expected_journal(sprint1: Scenario) -> None:
    # The table in the PR description and the Sprint 1 demo come from here.
    batches = engine().post_events(generate_events(sprint1, 42, policies()))
    assert [
        (
            b.posting_rule_id,
            b.posting_date,
            [(ln.gl_account, ln.side.value, str(ln.amount.amount)) for ln in b.lines],
        )
        for b in batches
    ] == [
        (
            "cash_deposited",
            date(2026, 3, 2),
            [("1000", "DEBIT", "25000.00"), ("2100", "CREDIT", "25000.00")],
        ),
        (
            "trade_executed.buy",
            date(2026, 3, 3),
            [
                ("2100", "DEBIT", "3147.15"),
                ("2200", "CREDIT", "3142.20"),
                ("4100", "CREDIT", "4.95"),
                ("5100", "DEBIT", "0.60"),  # clearing fee: 60 shares x 0.01
                ("2200", "CREDIT", "0.60"),
            ],
        ),
        (
            "trade_executed.buy",
            date(2026, 3, 3),
            [
                ("2100", "DEBIT", "2101.35"),
                ("2200", "CREDIT", "2096.40"),
                ("4100", "CREDIT", "4.95"),
                ("5100", "DEBIT", "0.40"),  # clearing fee: 40 shares x 0.01
                ("2200", "CREDIT", "0.40"),
            ],
        ),
        (
            "trade_settled.buy",
            date(2026, 3, 4),
            [("2200", "DEBIT", "5238.60"), ("1000", "CREDIT", "5238.60")],
        ),
    ]
