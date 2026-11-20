"""External records: what the outside world reports back (FR-SIM-02).

A clearing firm, a custodian, a bank, a fee system, an FX desk, and a price
vendor each report on the brokerage's activity. This module derives their
records **from the canonical events only**, through the transformation
documented here, and never from internal derived tables (ledger lines, warehouse
models). Reconciliation (Phase 4) therefore compares two independently produced
sources. Defects are injected afterwards (``defects.py``).

Each record, the canonical event it comes from, and the broker reference it
carries:

- clearing confirmation: one per ``trade_executed`` (``execution_id``)
- settlement confirmation: one per ``trade_settled`` and ``exercise_settled``
  (``settlement_id``)
- bank statement line: one per ``cash_deposited``, ``cash_withdrawn``,
  ``trade_settled``, ``exercise_settled``, and ``dividend_paid`` (``cash_movement_id``,
  ``settlement_id``, or ``dividend_id``: the depository pays a dividend's total
  in one movement, ADR 013)
- custody snapshot: the running settled quantity per account x instrument, on
  each ledger business day while it is non-zero; exercised and expired options
  leave it on the exercise or expiry date (roadmap 5C-b)
- fee record: one per ``trade_executed`` with a clearing fee (``execution_id``)
- FX confirmation: one per ``fx_converted`` (``fx_conversion_id``)
- price feed record: one per ``price_updated``

Bank statement amounts are signed from the bank account's view: deposits,
SELL settlements, and dividends are credits (+), and withdrawals and BUY
settlements are debits (-). They sit on the value date: the business date for
cash movements, the settlement date for settlements, and the pay date for
dividends.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from northstar.common.money import Currency
from northstar.domain.calendar import MarketCalendar, business_date
from northstar.domain.events import (
    CashDeposited,
    CashWithdrawn,
    DividendPaid,
    Event,
    ExerciseSettled,
    FxConverted,
    FxRate,
    NonNegativeAmount,
    OptionExercised,
    OptionExpired,
    PositiveAmount,
    Price,
    PriceUpdated,
    Quantity,
    Side,
    TradeExecuted,
    TradeSettled,
)

SignedAmount = Annotated[
    Decimal,
    Field(max_digits=18, decimal_places=2),
    PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json"),
]
"""A signed monetary amount in minor units (bank statement convention)."""


EXTERNAL_RECORD_SCHEMA_VERSION: Final = 1
"""Version of every external record contract (``contracts/external/*.v1.schema.json``)."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ClearingConfirmation(_Frozen):
    """The clearing firm's confirmation of one execution."""

    record_type: Literal["clearing_confirmation"] = "clearing_confirmation"
    record_id: str
    broker_reference: str
    account_id: str
    instrument_id: str
    side: Side
    quantity: Quantity
    price: Price
    currency: Currency
    trade_date: date


class SettlementConfirmation(_Frozen):
    """The depository's confirmation that an obligation settled."""

    record_type: Literal["settlement_confirmation"] = "settlement_confirmation"
    record_id: str
    broker_reference: str
    account_id: str
    instrument_id: str
    side: Side
    quantity: Quantity
    amount: PositiveAmount
    currency: Currency
    contractual_settlement_date: date
    actual_settlement_date: date


class BankStatementLine(_Frozen):
    """One movement on the firm's settlement bank account (GL 1000)."""

    record_type: Literal["bank_statement_line"] = "bank_statement_line"
    record_id: str
    reference: str
    value_date: date
    currency: Currency
    amount: SignedAmount
    description: str


class CustodySnapshot(_Frozen):
    """The custodian's end-of-day settled holding for an account and instrument."""

    record_type: Literal["custody_snapshot"] = "custody_snapshot"
    record_id: str
    snapshot_date: date
    account_id: str
    instrument_id: str
    settled_quantity: Quantity


class FeeRecord(_Frozen):
    """The clearing firm's fee charged for one execution (matched to GL 5100)."""

    record_type: Literal["fee_record"] = "fee_record"
    record_id: str
    broker_reference: str
    account_id: str
    trade_date: date
    currency: Currency
    amount: NonNegativeAmount


class FxConfirmation(_Frozen):
    """The FX desk's confirmation of a client conversion."""

    record_type: Literal["fx_confirmation"] = "fx_confirmation"
    record_id: str
    broker_reference: str
    account_id: str
    sell_amount: PositiveAmount
    sell_currency: Currency
    buy_amount: PositiveAmount
    buy_currency: Currency
    client_rate: FxRate
    trade_date: date


class PriceFeedRecord(_Frozen):
    """The price vendor's daily close for an instrument."""

    record_type: Literal["price_feed_record"] = "price_feed_record"
    record_id: str
    instrument_id: str
    price_date: date
    close_price: Price
    currency: Currency


type ExternalRecord = (
    ClearingConfirmation
    | SettlementConfirmation
    | BankStatementLine
    | CustodySnapshot
    | FeeRecord
    | FxConfirmation
    | PriceFeedRecord
)

RECORD_MODELS: dict[str, type[BaseModel]] = {
    "clearing_confirmations": ClearingConfirmation,
    "settlement_confirmations": SettlementConfirmation,
    "bank_statement_lines": BankStatementLine,
    "custody_snapshots": CustodySnapshot,
    "fee_records": FeeRecord,
    "fx_confirmations": FxConfirmation,
    "price_feed": PriceFeedRecord,
}
"""External record files (``external/<name>.jsonl``) and their models."""

_PREFIX = {
    "clearing_confirmations": "clr",
    "settlement_confirmations": "stc",
    "bank_statement_lines": "bnk",
    "custody_snapshots": "cus",
    "fee_records": "fee",
    "fx_confirmations": "fxk",
    "price_feed": "pxf",
}


@dataclass(frozen=True, slots=True)
class ExternalRecords:
    """Every external record set, each in a deterministic order."""

    clearing_confirmations: tuple[ClearingConfirmation, ...]
    settlement_confirmations: tuple[SettlementConfirmation, ...]
    bank_statement_lines: tuple[BankStatementLine, ...]
    custody_snapshots: tuple[CustodySnapshot, ...]
    fee_records: tuple[FeeRecord, ...]
    fx_confirmations: tuple[FxConfirmation, ...]
    price_feed: tuple[PriceFeedRecord, ...]

    def by_name(self) -> dict[str, tuple[ExternalRecord, ...]]:
        """Each record set under its file name (``external/<name>.jsonl``)."""
        return {name: getattr(self, name) for name in RECORD_MODELS}


def external_record_id(record_set: str, seed: int, sequence: int) -> str:
    """Deterministic external ID: the reporting party's own reference."""
    return f"{_PREFIX[record_set]}_s{seed}_{sequence:06d}"


def derive_external_records(
    events: Sequence[Event], seed: int, ledger_calendar: MarketCalendar
) -> ExternalRecords:
    """Derive every external record set from the canonical events.

    Custody snapshots cover each ledger business day from the first settlement
    to the last canonical event's business date.
    """
    counters: dict[str, int] = defaultdict(int)

    def next_id(record_set: str) -> str:
        counters[record_set] += 1
        return external_record_id(record_set, seed, counters[record_set])

    clearing: list[ClearingConfirmation] = []
    settlements: list[SettlementConfirmation] = []
    bank: list[BankStatementLine] = []
    custody: list[CustodySnapshot] = []
    fees: list[FeeRecord] = []
    fx: list[FxConfirmation] = []
    prices: list[PriceFeedRecord] = []
    settled: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    settled_by_day: dict[date, dict[tuple[str, str], Decimal]] = {}
    for event in events:
        match event:
            case TradeExecuted(payload=p):
                clearing.append(
                    ClearingConfirmation(
                        record_id=next_id("clearing_confirmations"),
                        broker_reference=p.execution_id,
                        account_id=p.account_id,
                        instrument_id=p.instrument_id,
                        side=p.side,
                        quantity=p.quantity,
                        price=p.price,
                        currency=p.currency,
                        trade_date=p.trade_date,
                    )
                )
                if p.clearing_fee > 0:
                    fees.append(
                        FeeRecord(
                            record_id=next_id("fee_records"),
                            broker_reference=p.execution_id,
                            account_id=p.account_id,
                            trade_date=p.trade_date,
                            currency=p.currency,
                            amount=p.clearing_fee,
                        )
                    )
            case TradeSettled(payload=p) | ExerciseSettled(payload=p):
                # A trade's or an exercise's settlement: the depository delivers the
                # units and the strike or trade amount moves through the bank.
                settlements.append(
                    SettlementConfirmation(
                        record_id=next_id("settlement_confirmations"),
                        broker_reference=p.settlement_id,
                        account_id=p.account_id,
                        instrument_id=p.instrument_id,
                        side=p.side,
                        quantity=p.quantity,
                        amount=p.amount,
                        currency=p.currency,
                        contractual_settlement_date=p.settlement_date,
                        actual_settlement_date=p.settlement_date,
                    )
                )
                signed = -p.amount if p.side is Side.BUY else p.amount
                what = "exercise" if isinstance(event, ExerciseSettled) else "clearing"
                bank.append(
                    BankStatementLine(
                        record_id=next_id("bank_statement_lines"),
                        reference=p.settlement_id,
                        value_date=p.settlement_date,
                        currency=p.currency,
                        amount=signed,
                        description=f"{what} settlement {p.side.value.lower()}",
                    )
                )
                key = (p.account_id, p.instrument_id)
                settled[key] += p.quantity if p.side is Side.BUY else -p.quantity
                settled_by_day[p.settlement_date] = dict(settled)
            case OptionExercised(payload=p):
                # The exercised contracts leave the depository on the exercise date.
                settled[(p.account_id, p.option_instrument_id)] -= p.contracts
                settled_by_day[p.exercise_date] = dict(settled)
            case OptionExpired(payload=p):
                settled[(p.account_id, p.instrument_id)] -= p.contracts
                settled_by_day[p.expiry_date] = dict(settled)
            case CashDeposited(payload=p) | CashWithdrawn(payload=p):
                is_deposit = isinstance(event, CashDeposited)
                bank.append(
                    BankStatementLine(
                        record_id=next_id("bank_statement_lines"),
                        reference=p.cash_movement_id,
                        value_date=business_date(event.event_timestamp),
                        currency=p.currency,
                        amount=p.amount if is_deposit else -p.amount,
                        description="client deposit" if is_deposit else "client withdrawal",
                    )
                )
            case DividendPaid(payload=p):
                bank.append(
                    BankStatementLine(
                        record_id=next_id("bank_statement_lines"),
                        reference=p.dividend_id,
                        value_date=p.pay_date,
                        currency=p.currency,
                        amount=p.total_amount,
                        description="dividend payment",
                    )
                )
            case FxConverted(payload=p):
                fx.append(
                    FxConfirmation(
                        record_id=next_id("fx_confirmations"),
                        broker_reference=p.fx_conversion_id,
                        account_id=p.account_id,
                        sell_amount=p.sell_amount,
                        sell_currency=p.sell_currency,
                        buy_amount=p.buy_amount,
                        buy_currency=p.buy_currency,
                        client_rate=p.client_rate,
                        trade_date=business_date(event.event_timestamp),
                    )
                )
            case PriceUpdated(payload=p):
                prices.append(
                    PriceFeedRecord(
                        record_id=next_id("price_feed"),
                        instrument_id=p.instrument_id,
                        price_date=p.price_date,
                        close_price=p.close_price,
                        currency=p.currency,
                    )
                )
            case _:
                pass  # account, instrument, order, interest, and FX rate events have none

    if events and settled_by_day:
        start = min(settled_by_day)
        end = business_date(events[-1].event_timestamp)
        holdings: dict[tuple[str, str], Decimal] = {}
        day = start
        while day <= end:
            holdings = settled_by_day.get(day, holdings)
            if ledger_calendar.is_settlement_day(day):
                for (account_id, instrument_id), quantity in sorted(holdings.items()):
                    if quantity > 0:
                        custody.append(
                            CustodySnapshot(
                                record_id=next_id("custody_snapshots"),
                                snapshot_date=day,
                                account_id=account_id,
                                instrument_id=instrument_id,
                                settled_quantity=quantity,
                            )
                        )
            day += timedelta(days=1)
    return ExternalRecords(
        clearing_confirmations=tuple(clearing),
        settlement_confirmations=tuple(settlements),
        bank_statement_lines=tuple(bank),
        custody_snapshots=tuple(custody),
        fee_records=tuple(fees),
        fx_confirmations=tuple(fx),
        price_feed=tuple(prices),
    )
