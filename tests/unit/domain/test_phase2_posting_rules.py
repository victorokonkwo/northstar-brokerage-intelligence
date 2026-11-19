"""Hand-computed cases for the posting rules added in Phase 2 (domain model §9.2)."""

from datetime import date
from decimal import Decimal

import pytest

from northstar.common.money import Currency
from northstar.domain.accounting import EntrySide, JournalBatch
from northstar.domain.events import Side
from northstar.domain.fx import FxQuoteError

from factories import at_toronto, engine, execution, fx_conversion, interest, withdrawal

D, C = EntrySide.DEBIT, EntrySide.CREDIT


def lines(batch: JournalBatch) -> list[tuple[str, str | None, EntrySide, str, str]]:
    return [
        (ln.gl_account, ln.client_account_id, ln.side, str(ln.amount.amount), ln.amount.currency)
        for ln in batch.lines
    ]


def test_withdrawal() -> None:
    batch = engine().post(withdrawal(amount="250.00", currency=Currency.CAD))
    assert batch.posting_rule_id == "cash_withdrawn"
    assert lines(batch) == [
        ("2100", "acc_s1_000001", D, "250.00", "CAD"),
        ("1000", None, C, "250.00", "CAD"),
    ]


def test_fx_client_buys_usd() -> None:
    # Values from tests/unit/domain/test_fx_and_interest.py::TestQuote::test_client_buys_usd.
    batch = engine().post(fx_conversion("1000.00", Currency.CAD, "719.14"))
    assert batch.posting_rule_id == "fx_converted.buy_usd"
    assert lines(batch) == [
        ("2100", "acc_s1_000001", D, "1000.00", "CAD"),
        ("2300", None, C, "985.22", "CAD"),
        ("4200", None, C, "14.78", "CAD"),
        ("2300", None, D, "719.14", "USD"),
        ("2100", "acc_s1_000001", C, "719.14", "USD"),
    ]
    assert {ccy: (d.amount, c.amount) for ccy, (d, c) in batch.totals().items()} == {
        Currency.CAD: (Decimal("1000.00"), Decimal("1000.00")),
        Currency.USD: (Decimal("719.14"), Decimal("719.14")),
    }


def test_fx_client_sells_usd() -> None:
    batch = engine().post(
        fx_conversion("1000.00", Currency.USD, "1349.45", client_rate="1.3494500000")
    )
    assert batch.posting_rule_id == "fx_converted.buy_cad"
    assert lines(batch) == [
        ("2100", "acc_s1_000001", D, "1000.00", "USD"),
        ("2300", None, C, "1000.00", "USD"),
        ("2300", None, D, "1370.00", "CAD"),
        ("2100", "acc_s1_000001", C, "1349.45", "CAD"),
        ("4200", None, C, "20.55", "CAD"),
    ]


def test_fx_event_better_than_reference_is_refused() -> None:
    # The client would get more USD than the reference rate allows: negative spread.
    with pytest.raises(FxQuoteError, match="negative spread revenue"):
        engine().post(fx_conversion("1000.00", Currency.CAD, "740.00"))


def test_margin_interest() -> None:
    batch = engine().post(interest(amount="1.64", debit_balance="10000.00"))
    assert batch.posting_rule_id == "margin_interest_accrued"
    assert lines(batch) == [
        ("2100", "acc_s1_000001", D, "1.64", "CAD"),
        ("4300", None, C, "1.64", "CAD"),
    ]


def test_weekend_interest_posts_on_the_next_business_day() -> None:
    batch = engine().post(interest(accrual_date=date(2026, 3, 7)))  # Saturday
    assert (batch.business_date, batch.posting_date) == (date(2026, 3, 7), date(2026, 3, 9))


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
def test_clearing_fee_lines_come_last_and_balance(side: Side) -> None:
    batch = engine().post(
        execution(side=side, quantity="100", price="10.00", commission="4.95", clearing_fee="1.00")
    )
    assert lines(batch)[-2:] == [("5100", None, D, "1.00", "USD"), ("2200", None, C, "1.00", "USD")]
    (debits, credits) = batch.totals()[Currency.USD]
    assert debits == credits


def test_an_execution_without_a_clearing_fee_posts_exactly_as_v1() -> None:
    # Same event, same rule version, same line IDs as before clearing fees existed.
    with_zero = engine().post(execution(clearing_fee="0.00"))
    assert [ln.gl_account for ln in with_zero.lines] == ["2100", "2200", "4100"]
    assert with_zero.posting_rule_version == 1


def test_option_premium_uses_the_contract_multiplier() -> None:
    event = execution(quantity="2", price="3.45", commission="1.50")
    option = event.model_copy(
        update={"payload": event.payload.model_copy(update={"multiplier": 100})}
    )
    batch = engine().post(option)
    # 2 contracts x 3.45 x 100 = 690.00 premium, plus 1.50 commission.
    assert lines(batch) == [
        ("2100", "acc_s1_000001", D, "691.50", "USD"),
        ("2200", None, C, "690.00", "USD"),
        ("4100", None, C, "1.50", "USD"),
    ]


def test_new_rules_are_idempotent() -> None:
    for event in (
        withdrawal(when=at_toronto(date(2026, 3, 5))),
        fx_conversion("1000.00", Currency.CAD, "719.14"),
        interest(),
    ):
        assert engine().post(event) == engine().post(event)
