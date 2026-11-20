"""External records (FR-SIM-02) and the defect manifest as exact ground truth (FR-SIM-03)."""

from collections import Counter, defaultdict
from decimal import Decimal

import pytest
from pydantic import BaseModel

from northstar.common.money import Currency
from northstar.domain.accounting import CASH, EntrySide
from northstar.domain.events import (
    CashDeposited,
    CashWithdrawn,
    DividendPaid,
    Event,
    FxConverted,
    PriceUpdated,
    TradeExecuted,
    TradeSettled,
)
from northstar.domain.positions import PositionBook
from northstar.infrastructure.config_loader import load_policy
from northstar.infrastructure.event_files import serialize_record
from northstar.simulation.defects import DefectConfig, DefectType, InjectedRecords, inject_defects
from northstar.simulation.external import ExternalRecords, derive_external_records
from northstar.simulation.generator import DatasetConfig, generate_dataset

from factories import CONFIG, engine, policies, xtse

DEFECTS = CONFIG / "simulation" / "defects" / "dev.yaml"


@pytest.fixture(scope="module")
def events() -> tuple[Event, ...]:
    config = load_policy(CONFIG / "simulation" / "datasets" / "dev.yaml", DatasetConfig)
    return generate_dataset(config, 42, policies())


@pytest.fixture(scope="module")
def exact(events: tuple[Event, ...]) -> ExternalRecords:
    return derive_external_records(events, 42, xtse())


@pytest.fixture(scope="module")
def injected(events: tuple[Event, ...], exact: ExternalRecords) -> InjectedRecords:
    return inject_defects(exact, events, load_policy(DEFECTS, DefectConfig), 42, xtse())


class TestExactTransformation:
    def test_one_record_per_source_event(
        self, events: tuple[Event, ...], exact: ExternalRecords
    ) -> None:
        kinds = Counter(type(e) for e in events)
        executions = [e for e in events if isinstance(e, TradeExecuted)]
        assert len(exact.clearing_confirmations) == kinds[TradeExecuted]
        assert len(exact.settlement_confirmations) == kinds[TradeSettled]
        assert len(exact.fee_records) == sum(1 for e in executions if e.payload.clearing_fee > 0)
        assert len(exact.fx_confirmations) == kinds[FxConverted]
        assert len(exact.price_feed) == kinds[PriceUpdated]
        assert len(exact.bank_statement_lines) == (
            kinds[CashDeposited] + kinds[CashWithdrawn] + kinds[TradeSettled] + kinds[DividendPaid]
        )

    def test_clearing_confirmations_restate_the_executions(
        self, events: tuple[Event, ...], exact: ExternalRecords
    ) -> None:
        by_ref = {c.broker_reference: c for c in exact.clearing_confirmations}
        for e in events:
            if isinstance(e, TradeExecuted):
                c = by_ref[e.payload.execution_id]
                assert (
                    c.account_id,
                    c.instrument_id,
                    c.side,
                    c.quantity,
                    c.price,
                    c.trade_date,
                ) == (
                    e.payload.account_id,
                    e.payload.instrument_id,
                    e.payload.side,
                    e.payload.quantity,
                    e.payload.price,
                    e.payload.trade_date,
                )

    def test_bank_statement_equals_the_ledger_cash_account(
        self, events: tuple[Event, ...], exact: ExternalRecords
    ) -> None:
        # Independent cross-check: GL 1000 from the posting engine against the bank.
        ledger: dict[Currency, Decimal] = defaultdict(Decimal)
        for batch in engine().post_events(events):
            for line in batch.lines:
                if line.gl_account == CASH:
                    signed = (
                        line.amount.amount if line.side is EntrySide.DEBIT else -line.amount.amount
                    )
                    ledger[line.amount.currency] += signed
        bank: dict[Currency, Decimal] = defaultdict(Decimal)
        for b in exact.bank_statement_lines:
            bank[b.currency] += b.amount
        assert dict(bank) == dict(ledger)

    def test_last_custody_snapshot_equals_settled_positions(
        self, events: tuple[Event, ...], exact: ExternalRecords
    ) -> None:
        last = max(s.snapshot_date for s in exact.custody_snapshots)
        custody = {
            (s.account_id, s.instrument_id): s.settled_quantity
            for s in exact.custody_snapshots
            if s.snapshot_date == last
        }
        book = PositionBook().apply_all(events)
        settled = {k: s for k, (_, s) in book.positions().items() if s > 0}
        assert custody == settled


def _by_id(records: tuple[BaseModel, ...]) -> dict[str, str]:
    return {r.record_id: serialize_record(r) for r in records}  # type: ignore[attr-defined]


class TestDefectManifest:
    def test_all_six_defect_types_occur(self, injected: InjectedRecords) -> None:
        assert {d.defect_type for d in injected.manifest} == set(DefectType)

    def test_every_change_is_in_the_manifest_and_every_entry_is_a_change(
        self, exact: ExternalRecords, injected: InjectedRecords
    ) -> None:
        changed: set[str] = set()
        for name, before_records in exact.by_name().items():
            before, after = _by_id(before_records), _by_id(injected.records.by_name()[name])
            changed |= set(before) ^ set(after)  # dropped or added (duplicates)
            changed |= {i for i in set(before) & set(after) if before[i] != after[i]}
        listed = [i for d in injected.manifest for i in d.affected_record_ids]
        assert len(listed) == len(set(listed)), "a record appears in two manifest entries"
        assert set(listed) == changed

    def test_impacts_are_recomputed_independently(
        self, events: tuple[Event, ...], exact: ExternalRecords, injected: InjectedRecords
    ) -> None:
        executions = {
            e.payload.execution_id: e.payload for e in events if isinstance(e, TradeExecuted)
        }
        fees = {r.record_id: r for r in exact.fee_records}
        fees_after = {r.record_id: r for r in injected.records.fee_records}
        fx = {r.record_id: r for r in exact.fx_confirmations}
        fx_after = {r.record_id: r for r in injected.records.fx_confirmations}
        prices = {r.record_id: r for r in exact.price_feed}
        prices_after = {r.record_id: r for r in injected.records.price_feed}
        settlements = {r.broker_reference: r for r in exact.settlement_confirmations}
        for d in injected.manifest:
            (first, *_) = d.affected_record_ids
            if d.defect_type is DefectType.WRONG_FEE:
                assert d.impact_amount == fees_after[first].amount - fees[first].amount
            elif d.defect_type is DefectType.WRONG_FX_CONVERSION:
                assert d.impact_amount == fx[first].buy_amount - fx_after[first].buy_amount
            elif d.defect_type is DefectType.STALE_PRICE:
                assert d.impact_amount == abs(
                    prices[first].close_price - prices_after[first].close_price
                )
                assert d.impact_basis == "price_per_unit"
            elif d.defect_type is DefectType.DELAYED_SETTLEMENT:
                assert d.impact_amount == settlements[d.broker_reference].amount
            elif d.record_set == "clearing_confirmations":  # dropped or duplicate
                p = executions[d.broker_reference]
                assert d.impact_amount == (p.quantity * p.price * p.multiplier).quantize(
                    Decimal("0.01")
                )

    def test_expected_exceptions_follow_domain_model_7_2(self, injected: InjectedRecords) -> None:
        expected = {
            (DefectType.DROPPED_RECORD, "clearing_confirmations"): "MISSING_EXTERNAL",
            (DefectType.DROPPED_RECORD, "settlement_confirmations"): "MISSING_EXTERNAL",
            (DefectType.DROPPED_RECORD, "bank_statement_lines"): "CASH_BREAK",
            (DefectType.DUPLICATE_CLEARING_MESSAGE, "clearing_confirmations"): "DUPLICATE_EXTERNAL",
            (DefectType.WRONG_FEE, "fee_records"): "FEE_MISMATCH",
            (DefectType.DELAYED_SETTLEMENT, "settlement_confirmations"): "SETTLEMENT_LATE",
            (DefectType.WRONG_FX_CONVERSION, "fx_confirmations"): "FX_MISMATCH",
            (DefectType.STALE_PRICE, "price_feed"): "STALE_PRICE",
        }
        for d in injected.manifest:
            assert d.expected_exception == expected[(d.defect_type, d.record_set)]

    def test_delayed_settlement_moves_the_confirmation_and_its_bank_line(
        self, exact: ExternalRecords, injected: InjectedRecords
    ) -> None:
        bank = {b.record_id: b for b in injected.records.bank_statement_lines}
        confirmations = {c.record_id: c for c in injected.records.settlement_confirmations}
        for d in (m for m in injected.manifest if m.defect_type is DefectType.DELAYED_SETTLEMENT):
            confirmation_id, bank_id = d.affected_record_ids
            c = confirmations[confirmation_id]
            assert c.actual_settlement_date > c.contractual_settlement_date
            assert bank[bank_id].value_date == c.actual_settlement_date


def test_zero_rates_change_nothing(events: tuple[Event, ...], exact: ExternalRecords) -> None:
    config = load_policy(DEFECTS, DefectConfig)
    zero = config.model_copy(
        update={
            "rates": config.rates.model_copy(
                update=dict.fromkeys(type(config.rates).model_fields, Decimal(0))
            )
        }
    )
    result = inject_defects(exact, events, zero, 42, xtse())
    assert result.manifest == ()
    assert result.records == exact


def test_injection_is_deterministic(events: tuple[Event, ...], exact: ExternalRecords) -> None:
    config = load_policy(DEFECTS, DefectConfig)
    assert inject_defects(exact, events, config, 42, xtse()) == inject_defects(
        exact, events, config, 42, xtse()
    )


def test_an_inverted_delay_range_is_refused(
    events: tuple[Event, ...], exact: ExternalRecords
) -> None:
    config = load_policy(DEFECTS, DefectConfig).model_copy(update={"delay_min_business_days": 4})
    with pytest.raises(ValueError, match="delay_min_business_days exceeds"):
        inject_defects(exact, events, config, 42, xtse())
