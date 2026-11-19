"""Inject labelled defects into external records (FR-SIM-03).

Each defect changes the external records in one known way and is written to
the **defect manifest**, which is the ground truth that reconciliation
(Phase 4) must find. Defects are drawn from their own random stream
(``SeedSequence([seed, DEFECT_STREAM])``), separate from the generator's, so
changing defect rates never changes the canonical events. A record receives
at most one defect.

Each defect, with the change it makes, the exception reconciliation should
raise (domain model §7.2), and its impact:

- ``DROPPED_RECORD`` on a clearing confirmation: removed -> ``MISSING_EXTERNAL``;
  impact = the execution's gross amount.
- ``DROPPED_RECORD`` on a settlement confirmation: removed -> ``MISSING_EXTERNAL``;
  impact = the settlement amount.
- ``DROPPED_RECORD`` on a bank statement line: removed -> ``CASH_BREAK``;
  impact = the absolute line amount.
- ``DUPLICATE_CLEARING_MESSAGE``: a clearing confirmation sent twice (new record
  ID, same broker reference) -> ``DUPLICATE_EXTERNAL``; impact = gross amount.
- ``WRONG_FEE``: a fee record's amount x ``wrong_fee_factor`` -> ``FEE_MISMATCH``;
  impact = the absolute fee difference.
- ``DELAYED_SETTLEMENT``: a settlement confirmation's actual date, and its bank
  line's value date, move N ledger business days later -> ``SETTLEMENT_LATE``;
  impact = the settlement amount.
- ``WRONG_FX_CONVERSION``: an FX confirmation's buy amount x (1 - shift) ->
  ``FX_MISMATCH``; impact = the absolute buy-amount difference.
- ``STALE_PRICE``: a price feed record repeats the previous close ->
  ``STALE_PRICE`` (the INV-08 flag); impact = the absolute price difference
  **per unit**.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, PlainSerializer

from northstar.common.money import Currency
from northstar.domain.calendar import MarketCalendar
from northstar.domain.events import Event, TradeExecuted
from northstar.domain.trading import gross_amount
from northstar.simulation.external import (
    BankStatementLine,
    ClearingConfirmation,
    ExternalRecords,
    FeeRecord,
    FxConfirmation,
    PriceFeedRecord,
    SettlementConfirmation,
)

DEFECT_STREAM = 2
"""Second SeedSequence entropy word: keeps defect draws independent of the generator."""

Rate = Annotated[Decimal, Field(ge=0, le=1)]
ImpactAmount = Annotated[
    Decimal,
    Field(ge=0, max_digits=18, decimal_places=8),
    PlainSerializer(lambda v: format(v, "f"), return_type=str, when_used="json"),
]


class DefectType(StrEnum):
    """The six FR-SIM-03 defect kinds."""

    DROPPED_RECORD = "DROPPED_RECORD"
    DUPLICATE_CLEARING_MESSAGE = "DUPLICATE_CLEARING_MESSAGE"
    WRONG_FEE = "WRONG_FEE"
    DELAYED_SETTLEMENT = "DELAYED_SETTLEMENT"
    WRONG_FX_CONVERSION = "WRONG_FX_CONVERSION"
    STALE_PRICE = "STALE_PRICE"


class DefectRates(BaseModel):
    """Per-record probabilities for each defect."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    dropped_clearing_confirmation: Rate
    dropped_settlement_confirmation: Rate
    dropped_bank_statement_line: Rate
    duplicate_clearing_confirmation: Rate
    wrong_fee: Rate
    delayed_settlement: Rate
    wrong_fx_conversion: Rate
    stale_price: Rate


class DefectConfig(BaseModel):
    """Versioned defect-injection configuration (illustrative)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    illustrative: bool
    rates: DefectRates
    wrong_fee_factor: Decimal = Field(gt=1)
    wrong_fx_buy_shift: Decimal = Field(gt=0, lt=1)
    delay_min_business_days: Annotated[int, Field(ge=1)]
    delay_max_business_days: Annotated[int, Field(ge=1)]


class DefectEntry(BaseModel):
    """One manifest line: what was changed, what reconciliation should find, and why it matters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    defect_id: str
    defect_type: DefectType
    record_set: str
    affected_record_ids: tuple[str, ...]
    broker_reference: str
    expected_exception: str
    impact_amount: ImpactAmount
    impact_currency: Currency
    impact_basis: Literal["amount", "price_per_unit"]
    defect_config_version: str


@dataclass(frozen=True, slots=True)
class InjectedRecords:
    """External records after injection, and the manifest of what was injected."""

    records: ExternalRecords
    manifest: tuple[DefectEntry, ...]


class _Injector:
    def __init__(self, config: DefectConfig, seed: int) -> None:
        self.config = config
        self.seed = seed
        self.rng = np.random.Generator(
            np.random.PCG64(np.random.SeedSequence([seed, DEFECT_STREAM]))
        )
        self.manifest: list[DefectEntry] = []

    def hit(self, rate: Decimal) -> bool:
        return Decimal(float(self.rng.random())) < rate

    def log(
        self,
        defect_type: DefectType,
        record_set: str,
        affected: tuple[str, ...],
        reference: str,
        expected: str,
        impact: Decimal,
        currency: Currency,
        basis: Literal["amount", "price_per_unit"] = "amount",
    ) -> None:
        self.manifest.append(
            DefectEntry(
                defect_id=f"dft_s{self.seed}_{len(self.manifest) + 1:06d}",
                defect_type=defect_type,
                record_set=record_set,
                affected_record_ids=affected,
                broker_reference=reference,
                expected_exception=expected,
                impact_amount=impact,
                impact_currency=currency,
                impact_basis=basis,
                defect_config_version=self.config.version,
            )
        )


def inject_defects(
    records: ExternalRecords,
    events: Sequence[Event],
    config: DefectConfig,
    seed: int,
    ledger_calendar: MarketCalendar,
) -> InjectedRecords:
    """Apply defects to a copy of ``records`` and return them with the manifest.

    The canonical ``events`` are only read, to value impacts (for example an
    execution's gross amount needs its multiplier).

    Raises:
        ValueError: ``delay_min_business_days`` exceeds ``delay_max_business_days``.
    """
    if config.delay_min_business_days > config.delay_max_business_days:
        msg = "delay_min_business_days exceeds delay_max_business_days"
        raise ValueError(msg)
    inj = _Injector(config, seed)
    clearing = _clearing(inj, records.clearing_confirmations, events, seed)
    settlements, delayed = _settlements(inj, records.settlement_confirmations, ledger_calendar)
    bank = _bank(inj, records.bank_statement_lines, settlements, delayed)
    return InjectedRecords(
        records=ExternalRecords(
            clearing_confirmations=clearing,
            settlement_confirmations=settlements,
            bank_statement_lines=bank,
            custody_snapshots=records.custody_snapshots,
            fee_records=_fees(inj, records.fee_records),
            fx_confirmations=_fx(inj, records.fx_confirmations),
            price_feed=_prices(inj, records.price_feed),
        ),
        manifest=tuple(inj.manifest),
    )


def _clearing(
    inj: _Injector,
    records: tuple[ClearingConfirmation, ...],
    events: Sequence[Event],
    seed: int,
) -> tuple[ClearingConfirmation, ...]:
    executions = {e.payload.execution_id: e.payload for e in events if isinstance(e, TradeExecuted)}
    rates = inj.config.rates
    out: list[ClearingConfirmation] = []
    sequence = len(records)
    for record in records:
        p = executions[record.broker_reference]
        gross = gross_amount(p.quantity, p.price, p.multiplier, p.currency).amount
        if inj.hit(rates.dropped_clearing_confirmation):
            inj.log(
                DefectType.DROPPED_RECORD,
                "clearing_confirmations",
                (record.record_id,),
                record.broker_reference,
                "MISSING_EXTERNAL",
                gross,
                record.currency,
            )
            continue
        out.append(record)
        if inj.hit(rates.duplicate_clearing_confirmation):
            sequence += 1
            copy = record.model_copy(update={"record_id": f"clr_s{seed}_{sequence:06d}"})
            out.append(copy)
            inj.log(
                DefectType.DUPLICATE_CLEARING_MESSAGE,
                "clearing_confirmations",
                (copy.record_id,),
                record.broker_reference,
                "DUPLICATE_EXTERNAL",
                gross,
                record.currency,
            )
    return tuple(out)


def _settlements(
    inj: _Injector,
    records: tuple[SettlementConfirmation, ...],
    calendar: MarketCalendar,
) -> tuple[tuple[SettlementConfirmation, ...], dict[str, date]]:
    rates, config = inj.config.rates, inj.config
    out: list[SettlementConfirmation] = []
    delayed: dict[str, date] = {}
    for record in records:
        if inj.hit(rates.dropped_settlement_confirmation):
            inj.log(
                DefectType.DROPPED_RECORD,
                "settlement_confirmations",
                (record.record_id,),
                record.broker_reference,
                "MISSING_EXTERNAL",
                record.amount,
                record.currency,
            )
            continue
        kept = record
        if inj.hit(rates.delayed_settlement):
            days = int(
                inj.rng.integers(config.delay_min_business_days, config.delay_max_business_days + 1)
            )
            late = calendar.add_settlement_days(record.contractual_settlement_date, days)
            delayed[record.broker_reference] = late
            kept = record.model_copy(update={"actual_settlement_date": late})
        out.append(kept)
    return tuple(out), delayed


def _bank(
    inj: _Injector,
    records: tuple[BankStatementLine, ...],
    settlements: tuple[SettlementConfirmation, ...],
    delayed: dict[str, date],
) -> tuple[BankStatementLine, ...]:
    out: list[BankStatementLine] = []
    moved: dict[str, str] = {}
    for record in records:
        if record.reference in delayed:
            # The bank moves the cash when the settlement actually happens.
            out.append(record.model_copy(update={"value_date": delayed[record.reference]}))
            moved[record.reference] = record.record_id
            continue
        if inj.hit(inj.config.rates.dropped_bank_statement_line):
            inj.log(
                DefectType.DROPPED_RECORD,
                "bank_statement_lines",
                (record.record_id,),
                record.reference,
                "CASH_BREAK",
                abs(record.amount),
                record.currency,
            )
            continue
        out.append(record)
    by_reference = {s.broker_reference: s for s in settlements}
    for reference in delayed:
        confirmation = by_reference[reference]
        inj.log(
            DefectType.DELAYED_SETTLEMENT,
            "settlement_confirmations",
            (confirmation.record_id, moved[reference]),
            reference,
            "SETTLEMENT_LATE",
            confirmation.amount,
            confirmation.currency,
        )
    return tuple(out)


def _fees(inj: _Injector, records: tuple[FeeRecord, ...]) -> tuple[FeeRecord, ...]:
    out: list[FeeRecord] = []
    for record in records:
        kept = record
        if inj.hit(inj.config.rates.wrong_fee):
            wrong = (record.amount * inj.config.wrong_fee_factor).quantize(Decimal("0.01"))
            inj.log(
                DefectType.WRONG_FEE,
                "fee_records",
                (record.record_id,),
                record.broker_reference,
                "FEE_MISMATCH",
                wrong - record.amount,
                record.currency,
            )
            kept = record.model_copy(update={"amount": wrong})
        out.append(kept)
    return tuple(out)


def _fx(inj: _Injector, records: tuple[FxConfirmation, ...]) -> tuple[FxConfirmation, ...]:
    out: list[FxConfirmation] = []
    for record in records:
        kept = record
        if inj.hit(inj.config.rates.wrong_fx_conversion):
            shift = 1 - inj.config.wrong_fx_buy_shift
            wrong = (record.buy_amount * shift).quantize(Decimal("0.01"))
            inj.log(
                DefectType.WRONG_FX_CONVERSION,
                "fx_confirmations",
                (record.record_id,),
                record.broker_reference,
                "FX_MISMATCH",
                record.buy_amount - wrong,
                record.buy_currency,
            )
            kept = record.model_copy(update={"buy_amount": wrong})
        out.append(kept)
    return tuple(out)


def _prices(inj: _Injector, records: tuple[PriceFeedRecord, ...]) -> tuple[PriceFeedRecord, ...]:
    out: list[PriceFeedRecord] = []
    previous: dict[str, PriceFeedRecord] = {}
    for record in records:
        prior = previous.get(record.instrument_id)
        previous[record.instrument_id] = record
        kept = record
        moved = prior is not None and prior.close_price != record.close_price
        if prior is not None and moved and inj.hit(inj.config.rates.stale_price):
            inj.log(
                DefectType.STALE_PRICE,
                "price_feed",
                (record.record_id,),
                f"{record.instrument_id}:{record.price_date.isoformat()}",
                "STALE_PRICE",
                abs(record.close_price - prior.close_price),
                record.currency,
                "price_per_unit",
            )
            kept = record.model_copy(update={"close_price": prior.close_price})
        out.append(kept)
    return tuple(out)
