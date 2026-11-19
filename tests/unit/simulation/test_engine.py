"""The simulation engine: the Phase 2 flagship golden journal and every refusal."""

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from northstar.common.exceptions import NotATradingDayError, ScenarioError
from northstar.common.money import Currency, Money
from northstar.domain.events import (
    AccountType,
    Event,
    OrderCancelled,
    OrderType,
    Side,
    TradeSettled,
)
from northstar.domain.positions import PositionBook, ShortSaleError
from northstar.domain.settlement import AssetClass
from northstar.infrastructure.config_loader import load_policy
from northstar.simulation.engine import (
    ClosePrice,
    Convert,
    Deposit,
    Fill,
    InstrumentSpec,
    OpenAccount,
    PlaceOrder,
    SimulationEngine,
    Withdraw,
)
from northstar.simulation.scenario import Scenario, generate_events

from factories import CONFIG, engine, policies

FLAGSHIP = CONFIG / "simulation" / "flagship_phase2.yaml"


def summary(events: tuple[Event, ...]) -> list[tuple[str, str, list[tuple[str, str, str, str]]]]:
    return [
        (
            b.posting_rule_id,
            b.business_date.isoformat(),
            [
                (ln.gl_account, ln.side.value[0], str(ln.amount.amount), ln.amount.currency)
                for ln in b.lines
            ],
        )
        for b in engine().post_events(events)
    ]


@pytest.fixture(scope="module")
def events() -> tuple[Event, ...]:
    return generate_events(load_policy(FLAGSHIP, Scenario), 42, policies())


class TestFlagship:
    def test_event_mix(self, events: tuple[Event, ...]) -> None:
        assert Counter(e.event_type.value for e in events) == {
            "instrument_listed": 2,
            "account_created": 1,
            "cash_deposited": 1,
            "fx_converted": 1,
            "order_placed": 3,
            "trade_executed": 4,
            "trade_settled": 3,
            "margin_interest_accrued": 7,  # 2026-03-04 .. 2026-03-10, every calendar day
        }

    def test_golden_journal(self, events: tuple[Event, ...]) -> None:
        journal = summary(events)
        assert journal[:5] == [
            (
                "cash_deposited",
                "2026-03-02",
                [("1000", "D", "25000.00", "CAD"), ("2100", "C", "25000.00", "CAD")],
            ),
            (
                "fx_converted.buy_usd",
                "2026-03-02",
                [
                    # 10000 / (1.37 x 1.015) = 7191.399 -> 7191.40; x 1.37 = 9852.218 -> 9852.22
                    ("2100", "D", "10000.00", "CAD"),
                    ("2300", "C", "9852.22", "CAD"),
                    ("4200", "C", "147.78", "CAD"),
                    ("2300", "D", "7191.40", "USD"),
                    ("2100", "C", "7191.40", "USD"),
                ],
            ),
            (
                "trade_executed.buy",
                "2026-03-03",
                [
                    ("2100", "D", "3676.95", "USD"),
                    ("2200", "C", "3672.00", "USD"),
                    ("4100", "C", "4.95", "USD"),
                    ("5100", "D", "0.60", "USD"),
                    ("2200", "C", "0.60", "USD"),
                ],
            ),
            (
                "trade_executed.buy",
                "2026-03-03",
                [
                    ("2100", "D", "2454.95", "USD"),
                    ("2200", "C", "2450.00", "USD"),
                    ("4100", "C", "4.95", "USD"),
                    ("5100", "D", "0.40", "USD"),
                    ("2200", "C", "0.40", "USD"),
                ],
            ),
            (
                "trade_executed.buy",
                "2026-03-04",
                [
                    ("2100", "D", "12284.95", "USD"),
                    ("2200", "C", "12280.00", "USD"),
                    ("4100", "C", "4.95", "USD"),
                    ("5100", "D", "2.00", "USD"),
                    ("2200", "C", "2.00", "USD"),
                ],
            ),
        ]
        interest = [lines[0][2] for rule, _, lines in journal if rule == "margin_interest_accrued"]
        # Day 1: USD debit 7191.40 - 6131.90 - 12284.95 = -11225.45;
        #        11225.45 x 6% / 365 = 1.845 -> 1.85.
        # Day 2: + option 1078.75 + 1.85 interest = 12306.05 -> 2.0229 -> 2.02 (and 2.02 after).
        assert interest == ["1.85", "2.02", "2.02", "2.02", "2.02", "2.02", "2.02"]
        option = next(
            lines
            for rule, d, lines in journal
            if d == "2026-03-05" and rule == "trade_executed.buy"
        )
        assert option == [
            ("2100", "D", "1078.75", "USD"),
            ("2200", "C", "1075.00", "USD"),
            ("4100", "C", "3.75", "USD"),
        ]

    def test_every_batch_balances(self, events: tuple[Event, ...]) -> None:
        for batch in engine().post_events(events):
            for debits, credits in batch.totals().values():
                assert debits == credits

    def test_positions_and_stock_record(self, events: tuple[Event, ...]) -> None:
        book = PositionBook().apply_all(events)
        quantities = sorted(q for q in book.positions().values())
        assert quantities == [(Decimal(5), Decimal(5)), (Decimal(300), Decimal(300))]
        assert all(line.balanced for line in book.stock_record())

    def test_settlements_are_t_plus_one_on_each_calendar(self, events: tuple[Event, ...]) -> None:
        settled = [e.payload for e in events if isinstance(e, TradeSettled)]
        assert [(s.trade_date, s.settlement_date) for s in settled] == [
            (date(2026, 3, 3), date(2026, 3, 4)),
            (date(2026, 3, 4), date(2026, 3, 5)),
            (date(2026, 3, 5), date(2026, 3, 6)),
        ]


# --- Refusals ----------------------------------------------------------------

T0 = datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
SYNA = InstrumentSpec(
    key="A",
    symbol="SYNA",
    name="Synthetic A",
    market="XTSE",
    asset_class=AssetClass.EQUITY,
    currency=Currency.CAD,
)
RATES = {date(2026, 3, 2): Decimal("1.3700000000")}


def _engine() -> SimulationEngine:
    return SimulationEngine(policies(), (SYNA,), RATES, 1, time(12, 0))


def _buy(at: datetime, quantity: str, price: str) -> PlaceOrder:
    q = Decimal(quantity)
    return PlaceOrder(
        at,
        "a",
        "A",
        Side.BUY,
        OrderType.MARKET,
        q,
        (Fill(at + timedelta(seconds=1), q, Decimal(price)),),
    )


def _opened(
    account_type: AccountType = AccountType.CASH, cash: str = "1000.00"
) -> SimulationEngine:
    sim = _engine()
    sim.submit(OpenAccount(T0, "a", account_type, Currency.CAD))
    sim.submit(Deposit(T0 + timedelta(minutes=1), "a", Money(Decimal(cash), Currency.CAD)))
    return sim


def test_cash_account_cannot_be_overdrawn() -> None:
    sim = _opened(cash="100.00")
    with pytest.raises(ScenarioError, match="would be overdrawn in CAD"):
        sim.submit(_buy(T0 + timedelta(hours=1), "10", "10.00"))  # 100.00 + 4.95 commission


def test_a_refused_action_poisons_the_engine() -> None:
    sim = _opened(cash="100.00")
    with pytest.raises(ScenarioError):
        sim.submit(_buy(T0 + timedelta(hours=1), "10", "10.00"))
    with pytest.raises(ScenarioError, match="cannot continue"):
        sim.submit(Deposit(T0 + timedelta(hours=2), "a", Money(Decimal("1.00"), Currency.CAD)))


def test_margin_account_may_borrow_up_to_the_guard_only() -> None:
    sim = _opened(AccountType.MARGIN, cash="0.01")
    sim.submit(_buy(T0 + timedelta(hours=1), "1000", "40.00"))  # borrows ~40,005
    assert sim.cash_balance("a", Currency.CAD).amount < 0
    with pytest.raises(ScenarioError, match=r"over the 50000\.00 simulator guard"):
        sim.submit(_buy(T0 + timedelta(hours=2), "300", "40.00"))


def test_withdrawals_and_conversions_need_the_money() -> None:
    sim = _opened(cash="100.00")
    with pytest.raises(ScenarioError, match=r"withdrawal of 100\.01 CAD exceeds"):
        sim.submit(Withdraw(T0 + timedelta(hours=1), "a", Money(Decimal("100.01"), Currency.CAD)))
    sim2 = _opened(cash="100.00")
    with pytest.raises(ScenarioError, match=r"conversion of 500\.00 CAD exceeds"):
        sim2.submit(Convert(T0 + timedelta(hours=1), "a", Money(Decimal("500.00"), Currency.CAD)))


def test_short_sales_are_refused() -> None:
    sim = _opened(cash="1000.00")
    at = T0 + timedelta(hours=1)
    sell = PlaceOrder(
        at,
        "a",
        "A",
        Side.SELL,
        OrderType.MARKET,
        Decimal(1),
        (Fill(at, Decimal(1), Decimal("10.00")),),
    )
    with pytest.raises(ShortSaleError):
        sim.submit(sell)


def test_actions_must_arrive_in_time_order() -> None:
    sim = _opened()
    with pytest.raises(ScenarioError, match="is before"):
        sim.submit(Deposit(T0, "a", Money(Decimal("1.00"), Currency.CAD)))


def test_fills_off_a_trading_day_are_refused() -> None:
    sim = _opened()
    saturday = datetime(2026, 3, 7, 15, 0, tzinfo=UTC)
    with pytest.raises(NotATradingDayError):
        sim.submit(_buy(saturday, "1", "10.00"))


def test_unknown_accounts_and_instruments_are_refused() -> None:
    sim = _opened()
    with pytest.raises(ScenarioError, match="unknown account"):
        sim.submit(Deposit(T0 + timedelta(hours=1), "nobody", Money(Decimal("1.00"), Currency.CAD)))
    sim2 = _opened()
    with pytest.raises(ScenarioError, match="unknown instrument"):
        sim2.submit(ClosePrice(T0 + timedelta(hours=1), "Z", date(2026, 3, 2), Decimal("1.00")))


def test_cancellations_need_an_unfilled_remainder() -> None:
    sim = _opened(cash="1000.00")
    at = T0 + timedelta(hours=1)
    order = PlaceOrder(
        at,
        "a",
        "A",
        Side.BUY,
        OrderType.MARKET,
        Decimal(5),
        (Fill(at, Decimal(5), Decimal("1.00")),),
        cancel_remainder_at=at + timedelta(minutes=1),
    )
    with pytest.raises(ScenarioError, match="nothing is left to cancel"):
        sim.submit(order)


def test_partial_fill_then_cancel_emits_the_remainder() -> None:
    sim = _opened(cash="1000.00")
    at = T0 + timedelta(hours=1)
    sim.submit(
        PlaceOrder(
            at,
            "a",
            "A",
            Side.BUY,
            OrderType.MARKET,
            Decimal(10),
            (Fill(at, Decimal(4), Decimal("1.00")),),
            cancel_remainder_at=at + timedelta(minutes=1),
        )
    )
    events = sim.finish(date(2026, 3, 2))
    (cancel,) = [e for e in events if isinstance(e, OrderCancelled)]
    assert cancel.payload.cancelled_quantity == Decimal(6)


def test_finish_can_only_run_once() -> None:
    sim = _opened()
    sim.finish(date(2026, 3, 2))
    with pytest.raises(ScenarioError, match="finished"):
        sim.finish(date(2026, 3, 2))
