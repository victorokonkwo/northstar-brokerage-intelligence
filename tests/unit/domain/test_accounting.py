"""Hand-computed cases for the v1 posting rules (domain model §9.2)."""

from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from northstar.common.exceptions import PolicyNotFoundError, UnbalancedJournalError
from northstar.common.money import Currency, Money
from northstar.domain.accounting import (
    NON_POSTING_EVENTS,
    ChartOfAccounts,
    EntrySide,
    JournalBatch,
    JournalLine,
    PostingEngine,
)
from northstar.domain.events import EventType, Side
from northstar.infrastructure.config_loader import load_policy
from northstar.simulation.scenario import Scenario, generate_events

from factories import (
    CONFIG,
    at_toronto,
    chart,
    deposit,
    dividend_paid,
    engine,
    execution,
    policies,
    settlement,
    xtse,
)

D, C = EntrySide.DEBIT, EntrySide.CREDIT


def lines(batch: JournalBatch) -> list[tuple[str, str | None, EntrySide, str]]:
    return [
        (line.gl_account, line.client_account_id, line.side, str(line.amount.amount))
        for line in batch.lines
    ]


def test_domain_model_worked_example_buy_10_at_250_with_1_commission() -> None:
    # Domain model §9.3: trade date T.
    batch = engine().post(
        execution(side=Side.BUY, quantity="10", price="250.00", commission="1.00")
    )
    assert lines(batch) == [
        ("2100", "acc_s1_000001", D, "2501.00"),
        ("2200", None, C, "2500.00"),
        ("4100", None, C, "1.00"),
    ]
    assert batch.totals() == {Currency.USD: (Money(Decimal("2501.00"), Currency.USD),) * 2}
    assert batch.posting_rule_id == "trade_executed.buy"
    assert batch.posting_rule_version == 1


def test_domain_model_worked_example_settlement_on_t_plus_1() -> None:
    batch = engine().post(settlement(side=Side.BUY, amount="2500.00"))
    assert lines(batch) == [("2200", None, D, "2500.00"), ("1000", None, C, "2500.00")]


def test_sell_credits_net_proceeds_to_the_client() -> None:
    batch = engine().post(
        execution(side=Side.SELL, quantity="10", price="250.00", commission="1.00")
    )
    assert lines(batch) == [
        ("1200", None, D, "2500.00"),
        ("2100", "acc_s1_000001", C, "2499.00"),
        ("4100", None, C, "1.00"),
    ]


def test_sell_with_commission_above_proceeds_debits_the_client() -> None:
    batch = engine().post(execution(side=Side.SELL, quantity="1", price="1.00", commission="4.95"))
    assert lines(batch) == [
        ("1200", None, D, "1.00"),
        ("2100", "acc_s1_000001", D, "3.95"),
        ("4100", None, C, "4.95"),
    ]


def test_sell_with_commission_equal_to_proceeds_posts_no_client_line() -> None:
    batch = engine().post(execution(side=Side.SELL, quantity="1", price="4.95", commission="4.95"))
    assert lines(batch) == [("1200", None, D, "4.95"), ("4100", None, C, "4.95")]


def test_zero_commission_posts_no_revenue_line() -> None:
    batch = engine().post(execution(side=Side.BUY, quantity="2", price="10.00", commission="0.00"))
    assert lines(batch) == [("2100", "acc_s1_000001", D, "20.00"), ("2200", None, C, "20.00")]


def test_sell_settlement() -> None:
    batch = engine().post(settlement(side=Side.SELL, amount="2500.00"))
    assert lines(batch) == [("1000", None, D, "2500.00"), ("1200", None, C, "2500.00")]


def test_deposit() -> None:
    batch = engine().post(deposit(amount="25000.00", currency=Currency.CAD))
    assert lines(batch) == [("1000", None, D, "25000.00"), ("2100", "acc_s1_000001", C, "25000.00")]


@pytest.mark.parametrize(
    ("price", "expected_gross"),
    [
        ("0.335", "1.00"),  # 3 x 0.335 = 1.005, tie rounds to even (0)
        ("0.338", "1.01"),  # 1.014
        ("0.345", "1.04"),  # 1.035, tie rounds to even (4)
    ],
)
def test_gross_amount_is_rounded_half_even_once_per_execution(
    price: str, expected_gross: str
) -> None:
    batch = engine().post(execution(quantity="3", price=price, commission="0.00"))
    assert batch.lines[1].amount.amount == Decimal(expected_gross)


class TestPostingDate:
    def test_business_day_posts_on_the_same_day(self) -> None:
        batch = engine().post(deposit(when=at_toronto(date(2026, 3, 3))))
        assert (batch.business_date, batch.posting_date) == (date(2026, 3, 3), date(2026, 3, 3))

    def test_saturday_deposit_posts_on_monday(self) -> None:
        batch = engine().post(deposit(when=at_toronto(date(2026, 3, 7))))
        assert (batch.business_date, batch.posting_date) == (date(2026, 3, 7), date(2026, 3, 9))

    def test_banking_holiday_posts_on_the_next_settlement_day(self) -> None:
        # Remembrance Day: the TSX trades, but the ledger calendar is the CAD
        # settlement calendar, which is closed.
        batch = engine().post(deposit(when=at_toronto(date(2026, 11, 11))))
        assert batch.posting_date == date(2026, 11, 12)

    def test_late_evening_utc_uses_the_toronto_date(self) -> None:
        # 23:30 Toronto on Friday 2026-03-06 is 04:30 UTC on Saturday.
        batch = engine().post(deposit(when=at_toronto(date(2026, 3, 6), 23, 30)))
        assert (batch.business_date, batch.posting_date) == (date(2026, 3, 6), date(2026, 3, 6))

    def test_versions_are_recorded(self) -> None:
        batch = engine().post(deposit())
        assert batch.chart_version == chart().version
        assert batch.ledger_calendar_version == xtse().version


class TestIdempotency:
    def test_reposting_an_event_reproduces_every_id(self) -> None:
        event = execution()
        first, second = engine().post(event), engine().post(event)
        assert first == second
        assert len({line.journal_line_id for line in first.lines}) == len(first.lines)

    def test_different_events_get_different_ids(self) -> None:
        a = engine().post(execution(event_id="evt_s1_000002"))
        b = engine().post(execution(event_id="evt_s1_000009"))
        assert a.journal_batch_id != b.journal_batch_id
        assert not {x.journal_line_id for x in a.lines} & {x.journal_line_id for x in b.lines}


class TestJournalInvariants:
    def _line(
        self, n: int, side: EntrySide, amount: str, ccy: Currency = Currency.CAD
    ) -> JournalLine:
        return JournalLine(uuid4(), uuid4(), n, "1000", None, side, Money(Decimal(amount), ccy))

    def _batch(self, *batch_lines: JournalLine) -> JournalBatch:
        template = engine().post(deposit())
        return replace(template, lines=batch_lines)

    def test_unbalanced_batch_is_rejected(self) -> None:
        with pytest.raises(
            UnbalancedJournalError, match=r"unbalanced in CAD: debits 10\.00, credits 9\.99"
        ):
            self._batch(self._line(1, D, "10.00"), self._line(2, C, "9.99"))

    def test_balance_is_per_currency_not_across_currencies(self) -> None:
        with pytest.raises(UnbalancedJournalError):
            self._batch(
                self._line(1, D, "10.00", Currency.CAD), self._line(2, C, "10.00", Currency.USD)
            )

    def test_a_single_line_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least two lines"):
            self._batch(self._line(1, D, "10.00"))

    def test_line_numbers_must_run_in_order(self) -> None:
        with pytest.raises(ValueError, match=r"1\.\.n"):
            self._batch(self._line(2, D, "10.00"), self._line(1, C, "10.00"))

    @pytest.mark.parametrize(
        ("amount", "message"),
        [("0.00", "positive"), ("-1.00", "positive"), ("1.005", "minor units")],
    )
    def test_line_amounts_must_be_positive_minor_units(self, amount: str, message: str) -> None:
        with pytest.raises(ValueError, match=message):
            self._line(1, D, amount)


def test_engine_refuses_a_chart_missing_a_rule_account() -> None:
    incomplete = ChartOfAccounts.model_validate(
        chart().model_dump()
        | {"accounts": [a for a in chart().model_dump()["accounts"] if a["code"] != "4100"]}
    )
    with pytest.raises(PolicyNotFoundError, match="4100"):
        PostingEngine(incomplete, xtse())


def test_post_events_skips_only_declared_non_posting_types() -> None:
    events = generate_events(
        load_policy(CONFIG / "simulation" / "sprint1_scenario.yaml", Scenario), 1, policies()
    )
    batches = engine().post_events(events)
    assert [b.source_event_type.value for b in batches] == [
        "cash_deposited",
        "trade_executed",
        "trade_executed",
        "trade_settled",
    ]


def test_a_dividend_credits_each_entitled_account_from_one_bank_receipt() -> None:
    batch = engine().post(dividend_paid([("acc_1", "100"), ("acc_2", "7")], "0.25"))
    assert batch.posting_rule_id == "dividend_paid"
    lines = [(ln.gl_account, ln.client_account_id, ln.side, ln.amount.amount) for ln in batch.lines]
    assert lines == [
        ("1000", None, EntrySide.DEBIT, Decimal("26.75")),
        ("2100", "acc_1", EntrySide.CREDIT, Decimal("25.00")),
        ("2100", "acc_2", EntrySide.CREDIT, Decimal("1.75")),
    ]


def test_a_dividend_declaration_posts_nothing() -> None:
    assert EventType.DIVIDEND_DECLARED in NON_POSTING_EVENTS
    assert EventType.DIVIDEND_PAID not in NON_POSTING_EVENTS


def test_post_events_refuses_an_event_type_without_a_rule() -> None:
    # A future event type must get a posting rule or be declared non-posting;
    # it is never skipped silently.
    unknown = SimpleNamespace(event_type="cash_teleported")
    with pytest.raises(ValueError, match="no posting rule for event type cash_teleported"):
        engine().post_events([unknown])  # type: ignore[list-item]
