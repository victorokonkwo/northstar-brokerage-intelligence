"""The simulation engine: the Phase 2 flagship golden journal and every refusal."""

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest

from northstar.common.exceptions import NotATradingDayError, ScenarioError
from northstar.common.money import Currency, Money
from northstar.domain.calendar import BUSINESS_TIMEZONE, business_date
from northstar.domain.events import (
    AccountStatus,
    AccountType,
    AccountUpdated,
    AccountUpdateReason,
    DividendDeclared,
    DividendPaid,
    Event,
    FxConverted,
    FxRatePublished,
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
    DeclareDividend,
    Deposit,
    Fill,
    InstrumentSpec,
    OpenAccount,
    PlaceOrder,
    SimulationEngine,
    UpdateAccount,
    Withdraw,
)
from northstar.simulation.scenario import Scenario, generate_events

from factories import CONFIG, engine, expected_client_cash, policies

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
            "fx_rate_published": 7,  # one per reference rate in the scenario
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


class TestAccountUpdates:
    def test_margin_approval_lets_a_cash_account_borrow(self) -> None:
        sim = _opened(AccountType.CASH, cash="100.00")
        sim.submit(
            UpdateAccount(
                T0 + timedelta(minutes=5),
                "a",
                AccountType.MARGIN,
                AccountStatus.ACTIVE,
                AccountUpdateReason.MARGIN_APPROVED,
            )
        )
        assert sim.account_type("a") is AccountType.MARGIN
        sim.submit(_buy(T0 + timedelta(hours=1), "10", "10.00"))  # would overdraw a CASH account
        assert sim.cash_balance("a", Currency.CAD).amount < 0
        events = sim.finish(T0.date())
        (update,) = [e for e in events if isinstance(e, AccountUpdated)]
        assert update.payload.account_type is AccountType.MARGIN
        assert update.payload.reason is AccountUpdateReason.MARGIN_APPROVED

    def test_a_restricted_account_cannot_place_orders_until_lifted(self) -> None:
        sim = _opened(cash="1000.00")
        restrict = AccountUpdateReason.TRADING_RESTRICTED
        sim.submit(
            UpdateAccount(
                T0 + timedelta(minutes=5),
                "a",
                AccountType.CASH,
                AccountStatus.TRADING_RESTRICTED,
                restrict,
            )
        )
        assert sim.trading_restricted("a")
        with pytest.raises(ScenarioError, match="restricted from trading"):
            sim.submit(_buy(T0 + timedelta(hours=1), "1", "10.00"))
        lifted = _opened(cash="1000.00")
        for minutes, status, reason in (
            (5, AccountStatus.TRADING_RESTRICTED, restrict),
            (6, AccountStatus.ACTIVE, AccountUpdateReason.RESTRICTION_LIFTED),
        ):
            lifted.submit(
                UpdateAccount(
                    T0 + timedelta(minutes=minutes), "a", AccountType.CASH, status, reason
                )
            )
        lifted.submit(_buy(T0 + timedelta(hours=1), "1", "10.00"))
        assert not lifted.trading_restricted("a")

    def test_an_update_that_changes_nothing_is_refused(self) -> None:
        sim = _opened()
        with pytest.raises(ScenarioError, match="changes nothing"):
            sim.submit(
                UpdateAccount(
                    T0 + timedelta(minutes=5),
                    "a",
                    AccountType.CASH,
                    AccountStatus.ACTIVE,
                    AccountUpdateReason.RESTRICTION_LIFTED,
                )
            )

    def test_a_margin_account_with_a_loan_cannot_become_cash(self) -> None:
        sim = _opened(AccountType.MARGIN, cash="0.01")
        sim.submit(_buy(T0 + timedelta(hours=1), "10", "10.00"))
        with pytest.raises(ScenarioError, match="while it has a loan"):
            sim.submit(
                UpdateAccount(
                    T0 + timedelta(hours=2),
                    "a",
                    AccountType.CASH,
                    AccountStatus.ACTIVE,
                    AccountUpdateReason.MARGIN_APPROVED,
                )
            )


class TestFxPublication:
    def test_one_publication_per_rate_at_the_start_of_its_day(
        self, events: tuple[Event, ...]
    ) -> None:
        scenario = load_policy(FLAGSHIP, Scenario)
        published = [e for e in events if isinstance(e, FxRatePublished)]
        assert {p.payload.rate_date: p.payload.rate for p in published} == {
            r.date: r.rate for r in scenario.fx_rates
        }
        for p in published:
            local = p.event_timestamp.astimezone(BUSINESS_TIMEZONE)
            assert (local.date(), local.time()) == (p.payload.rate_date, time(0, 0))
            assert p.payload.fx_policy_version == policies().fx_policy.version

    def test_every_conversion_uses_a_rate_published_before_it(
        self, events: tuple[Event, ...]
    ) -> None:
        published = {e.payload.rate_date: e for e in events if isinstance(e, FxRatePublished)}
        conversions = [e for e in events if isinstance(e, FxConverted)]
        assert conversions
        for c in conversions:
            rate = published[c.payload.rate_date]
            assert rate.payload.rate == c.payload.reference_rate
            assert rate.event_timestamp <= c.event_timestamp

    def test_rates_after_the_finish_date_are_not_published(self) -> None:
        sim = SimulationEngine(
            policies(),
            (SYNA,),
            {date(2026, 3, 2): Decimal("1.37"), date(2026, 3, 3): Decimal("1.38")},
            1,
            time(12, 0),
        )
        sim.submit(OpenAccount(T0, "a", AccountType.CASH, Currency.CAD))
        events = sim.finish(date(2026, 3, 2))
        assert [e.payload.rate_date for e in events if isinstance(e, FxRatePublished)] == [
            date(2026, 3, 2)
        ]


class TestDividends:
    """Entitlement is the settled holding at the end of the record (ex-)date (ADR 013)."""

    RECORD, PAY = date(2026, 3, 5), date(2026, 3, 13)

    @staticmethod
    def at(day: int, hour: int = 15) -> datetime:
        return datetime(2026, 3, day, hour, 0, tzinfo=UTC)  # 10:00 or later in Toronto

    def trade(self, account: str, side: Side, quantity: str, day: int, minute: int) -> PlaceOrder:
        at = self.at(day) + timedelta(minutes=minute)
        q = Decimal(quantity)
        return PlaceOrder(
            at, account, "A", side, OrderType.MARKET, q, (Fill(at, q, Decimal("10.00")),)
        )

    def run(self, rate: str = "0.50", holders: bool = True) -> tuple[Event, ...]:
        sim = _engine()
        for n, account in enumerate("abcd"):
            sim.submit(
                OpenAccount(T0 + timedelta(minutes=n), account, AccountType.CASH, Currency.CAD)
            )
            sim.submit(
                Deposit(
                    T0 + timedelta(minutes=n, seconds=30),
                    account,
                    Money(Decimal(500), Currency.CAD),
                )
            )
        sim.submit(
            DeclareDividend(T0 + timedelta(minutes=10), "A", Decimal(rate), self.RECORD, self.PAY)
        )
        if holders:
            sim.submit(self.trade("c", Side.BUY, "6", 3, 0))  # bought, then sold before the ex-date
            sim.submit(self.trade("d", Side.BUY, "6", 3, 1))  # held, sold on the ex-date
            sim.submit(
                self.trade("a", Side.BUY, "10", 4, 0)
            )  # the day before: settles on the record date
            sim.submit(self.trade("c", Side.SELL, "6", 4, 1))
            sim.submit(self.trade("b", Side.BUY, "4", 5, 0))  # on the ex-date: settles after it
            sim.submit(self.trade("d", Side.SELL, "6", 5, 1))
        return sim.finish(date(2026, 3, 31))

    def test_the_holders_settled_at_the_record_date_are_paid(self) -> None:
        events = self.run()
        paid = [e for e in events if isinstance(e, DividendPaid)]
        assert len(paid) == 1
        p = paid[0].payload
        # Accounts open in order a, b, c, d: acc_s1_000001 to acc_s1_000004.
        assert [(a.account_id, a.quantity, a.amount) for a in p.allocations] == [
            ("acc_s1_000001", Decimal(10), Decimal("5.00")),  # a: bought the day before the ex-date
            (
                "acc_s1_000004",
                Decimal(6),
                Decimal("3.00"),
            ),  # d: sold on the ex-date, still entitled
        ]
        assert (p.total_amount, p.pay_date, p.record_date) == (
            Decimal("8.00"),
            self.PAY,
            self.RECORD,
        )
        declared = next(e for e in events if isinstance(e, DividendDeclared))
        assert paid[0].causation_id == declared.event_id
        assert declared.payload.ex_date == declared.payload.record_date == self.RECORD
        assert business_date(paid[0].event_timestamp) == self.PAY

    def test_entitlement_is_the_trade_date_holding_at_the_close_before_the_ex_date(self) -> None:
        events = self.run()
        before_ex = [
            e for e in events if business_date(e.event_timestamp) < self.RECORD
        ]  # T+1: everything traded by then has settled by the end of the record date
        book = PositionBook().apply_all(before_ex)
        p = next(e for e in events if isinstance(e, DividendPaid)).payload
        held = {account: td for (account, _), (td, _) in book.positions().items() if td > 0}
        assert {a.account_id: a.quantity for a in p.allocations} == held

    def test_the_dividend_reaches_client_cash_through_the_ledger(self) -> None:
        events = self.run()
        cash = expected_client_cash(events)
        assert cash[("acc_s1_000001", Currency.CAD)] == Decimal(500) - Decimal("104.95") + Decimal(
            "5.00"
        )

    def test_no_holders_means_no_payment(self) -> None:
        events = self.run(holders=False)
        assert any(isinstance(e, DividendDeclared) for e in events)
        assert not any(isinstance(e, DividendPaid) for e in events)

    def test_the_record_date_must_be_a_business_day_and_the_instrument_an_equity(self) -> None:
        sim = _opened()
        with pytest.raises(ScenarioError, match="not a XTSE business day"):
            sim.submit(
                DeclareDividend(
                    T0 + timedelta(hours=1), "A", Decimal("0.50"), date(2026, 3, 7), self.PAY
                )
            )
