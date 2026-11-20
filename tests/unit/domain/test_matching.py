"""Staged matching and classification, checked against the defect manifest (NFR-03)."""

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal

import pytest

from northstar.common.money import Currency
from northstar.domain.accounting import CASH, EntrySide
from northstar.domain.calendar import business_date
from northstar.domain.events import (
    CashDeposited,
    CashWithdrawn,
    DividendPaid,
    Event,
    FxConverted,
    FxRatePublished,
    InstrumentListed,
    PriceUpdated,
    TradeExecuted,
    TradeSettled,
)
from northstar.domain.fx import reference_rate_on
from northstar.domain.matching import (
    ExternalCash,
    ExternalExecution,
    ExternalFee,
    ExternalFx,
    ExternalPosition,
    ExternalPrice,
    ExternalSettlement,
    Inputs,
    InternalCash,
    InternalExecution,
    InternalFee,
    InternalFx,
    InternalPosition,
    InternalPrice,
    InternalSettlement,
    match_cash,
    match_executions,
    match_positions,
    match_prices,
    reconcile_all,
)
from northstar.domain.reconciliation import (
    AppliedRate,
    ExceptionType,
    ReconciliationPolicy,
    Severity,
    classify,
)
from northstar.infrastructure.config_loader import load_policy
from northstar.simulation.defects import DefectConfig, DefectEntry, DefectType, inject_defects
from northstar.simulation.external import ExternalRecords, derive_external_records
from northstar.simulation.generator import DatasetConfig, generate_dataset

from factories import CONFIG, engine, policies, xtse

CENT = Decimal("0.01")
POLICY = load_policy(CONFIG / "reconciliation" / "policy.yaml", ReconciliationPolicy)
TOL = POLICY.tolerances


@dataclass
class Ours:
    executions: list[InternalExecution]
    settlements: list[InternalSettlement]
    fees: list[InternalFee]
    fx: list[InternalFx]
    prices: list[InternalPrice]
    multipliers: dict[str, int]
    cash: list[InternalCash]


def internal_side(events: tuple[Event, ...]) -> Ours:
    """Both internal sides built straight from the canonical events (the warehouse
    adapter builds the same from the dbt models)."""
    executions: list[InternalExecution] = []
    settlements: list[InternalSettlement] = []
    fees: list[InternalFee] = []
    fx: list[InternalFx] = []
    prices: list[InternalPrice] = []
    multipliers: dict[str, int] = {}
    references: dict[str, str] = {}
    for e in events:
        match e:
            case InstrumentListed(payload=p):
                multipliers[p.instrument_id] = p.multiplier
            case TradeExecuted(payload=p):
                gross = (p.quantity * p.price * p.multiplier).quantize(
                    CENT, rounding=ROUND_HALF_EVEN
                )
                executions.append(
                    InternalExecution(
                        p.execution_id,
                        p.account_id,
                        p.instrument_id,
                        p.side.value,
                        p.quantity,
                        p.price,
                        p.currency,
                        p.trade_date,
                        gross,
                    )
                )
                if p.clearing_fee > 0:
                    fees.append(
                        InternalFee(
                            p.execution_id, p.account_id, p.clearing_fee, p.currency, p.trade_date
                        )
                    )
            case TradeSettled(payload=p):
                settlements.append(
                    InternalSettlement(
                        p.settlement_id,
                        p.account_id,
                        p.instrument_id,
                        p.quantity,
                        p.amount,
                        p.currency,
                        p.settlement_date,
                    )
                )
                references[e.event_id] = p.settlement_id
            case CashDeposited(payload=p) | CashWithdrawn(payload=p):
                references[e.event_id] = p.cash_movement_id
            case DividendPaid(payload=p):
                references[e.event_id] = p.dividend_id
            case FxConverted(payload=p):
                fx.append(
                    InternalFx(
                        p.fx_conversion_id,
                        p.account_id,
                        p.sell_amount,
                        p.sell_currency,
                        p.buy_amount,
                        p.buy_currency,
                        business_date(e.event_timestamp),
                    )
                )
            case PriceUpdated(payload=p):
                prices.append(
                    InternalPrice(p.instrument_id, p.price_date, p.close_price, p.currency)
                )
            case _:
                pass
    cash: dict[tuple[str, Currency, date], Decimal] = defaultdict(Decimal)
    for batch in engine().post_events(events):
        for line in batch.lines:
            if line.gl_account == CASH:
                signed = line.amount.amount if line.side is EntrySide.DEBIT else -line.amount.amount
                key = (references[batch.source_event_id], line.amount.currency, batch.posting_date)
                cash[key] += signed
    return Ours(
        executions,
        settlements,
        fees,
        fx,
        prices,
        multipliers,
        [InternalCash(r, a, c, d) for (r, c, d), a in cash.items()],
    )


def positions(events: tuple[Event, ...], days: set[date]) -> list[InternalPosition]:
    settled: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    currency: dict[tuple[str, str], Currency] = {}
    moves = sorted(
        ((e.payload.settlement_date, e.payload) for e in events if isinstance(e, TradeSettled)),
        key=lambda x: x[0],
    )
    out: list[InternalPosition] = []
    k = 0
    for day in sorted(days):
        while k < len(moves) and moves[k][0] <= day:
            p = moves[k][1]
            settled[(p.account_id, p.instrument_id)] += (
                p.quantity if p.side.value == "BUY" else -p.quantity
            )
            currency[(p.account_id, p.instrument_id)] = p.currency
            k += 1
        out.extend(
            InternalPosition(a, i, day, q, currency[(a, i)]) for (a, i), q in settled.items()
        )
    return out


def build_inputs(events: tuple[Event, ...], r: ExternalRecords) -> Inputs:
    ours = internal_side(events)
    custody_days = {c.snapshot_date for c in r.custody_snapshots}
    return Inputs(
        executions=ours.executions,
        clearing=[
            ExternalExecution(
                c.record_id,
                c.broker_reference,
                c.account_id,
                c.instrument_id,
                c.side.value,
                c.quantity,
                c.price,
                c.currency,
                c.trade_date,
            )
            for c in r.clearing_confirmations
        ],
        multipliers=ours.multipliers,
        settlements=ours.settlements,
        settlement_confirmations=[
            ExternalSettlement(
                c.record_id,
                c.broker_reference,
                c.account_id,
                c.instrument_id,
                c.quantity,
                c.amount,
                c.currency,
                c.contractual_settlement_date,
                c.actual_settlement_date,
            )
            for c in r.settlement_confirmations
        ],
        cash=ours.cash,
        bank=[
            ExternalCash(b.record_id, b.reference, b.amount, b.currency, b.value_date)
            for b in r.bank_statement_lines
        ],
        positions=positions(events, custody_days),
        custody=[
            ExternalPosition(
                c.record_id, c.account_id, c.instrument_id, c.snapshot_date, c.settled_quantity
            )
            for c in r.custody_snapshots
        ],
        fees=ours.fees,
        fee_records=[
            ExternalFee(
                f.record_id, f.broker_reference, f.account_id, f.amount, f.currency, f.trade_date
            )
            for f in r.fee_records
        ],
        fx=ours.fx,
        fx_confirmations=[
            ExternalFx(
                f.record_id,
                f.broker_reference,
                f.account_id,
                f.sell_amount,
                f.sell_currency,
                f.buy_amount,
                f.buy_currency,
                f.trade_date,
            )
            for f in r.fx_confirmations
        ],
        prices=ours.prices,
        price_feed=[
            ExternalPrice(f.record_id, f.instrument_id, f.price_date, f.close_price, f.currency)
            for f in r.price_feed
        ],
    )


def applied_rates(events: tuple[Event, ...], days: set[date]) -> dict[date, AppliedRate]:
    rates = {e.payload.rate_date: e.payload for e in events if isinstance(e, FxRatePublished)}
    out = {}
    for day in days:
        if day >= min(rates):
            rate, rate_date = reference_rate_on(day, {d: p.rate for d, p in rates.items()})
            out[day] = AppliedRate(rate, rate_date, rates[rate_date].fx_policy_version)
    return out


@pytest.fixture(scope="module")
def dataset() -> tuple[tuple[Event, ...], ExternalRecords, tuple[DefectEntry, ...]]:
    events = generate_dataset(
        load_policy(CONFIG / "simulation" / "datasets" / "dev.yaml", DatasetConfig), 42, policies()
    )
    exact = derive_external_records(events, 42, xtse())
    injected = inject_defects(
        exact,
        events,
        load_policy(CONFIG / "simulation" / "defects" / "dev.yaml", DefectConfig),
        42,
        xtse(),
    )
    return events, injected.records, injected.manifest


class TestGroundTruth:
    def test_exact_external_records_reconcile_with_no_exceptions(
        self, dataset: tuple[tuple[Event, ...], ExternalRecords, tuple[DefectEntry, ...]]
    ) -> None:
        events, _, _ = dataset
        exact = derive_external_records(events, 42, xtse())
        result = reconcile_all(build_inputs(events, exact), TOL)
        assert result.breaks == ()
        assert len(result.matches) > 10_000

    def test_every_injected_defect_is_found_and_classified_and_nothing_else(
        self, dataset: tuple[tuple[Event, ...], ExternalRecords, tuple[DefectEntry, ...]]
    ) -> None:
        events, records, manifest = dataset
        result = reconcile_all(build_inputs(events, records), TOL)
        days = {b.business_date for b in result.breaks}
        exceptions = classify(result.breaks, POLICY, applied_rates(events, days))
        found = {(str(x.exception_type), x.reference): x for x in exceptions}
        expected = {(m.expected_exception, m.broker_reference): m for m in manifest}
        assert len(found) == len(exceptions), "two exceptions for one defect"
        assert set(found) == set(expected)
        for key, m in expected.items():
            x = found[key]
            assert (x.impact_amount, x.impact_currency, x.impact_basis) == (
                m.impact_amount,
                m.impact_currency,
                m.impact_basis,
            ), key
            if m.defect_type is not DefectType.DROPPED_RECORD:  # dropped records are gone
                assert set(x.external_record_ids) == set(m.affected_record_ids), key
        assert Counter(str(x.exception_type) for x in exceptions) == Counter(
            m.expected_exception for m in manifest
        )

    def test_a_rerun_gives_identical_exceptions(
        self, dataset: tuple[tuple[Event, ...], ExternalRecords, tuple[DefectEntry, ...]]
    ) -> None:
        events, records, _ = dataset
        runs = []
        for _ in range(2):
            result = reconcile_all(build_inputs(events, records), TOL)
            days = {b.business_date for b in result.breaks}
            runs.append(
                (result.matches, classify(result.breaks, POLICY, applied_rates(events, days)))
            )
        assert runs[0] == runs[1]

    def test_a_missing_dividend_receipt_is_a_cash_break_on_its_dividend(
        self, dataset: tuple[tuple[Event, ...], ExternalRecords, tuple[DefectEntry, ...]]
    ) -> None:
        # The depository's payment never shows on the bank statement (roadmap 5C-a).
        events, _, _ = dataset
        exact = derive_external_records(events, 42, xtse())
        paid = {e.payload.dividend_id: e.payload for e in events if isinstance(e, DividendPaid)}
        assert paid
        dropped = replace(
            exact,
            bank_statement_lines=tuple(
                b for b in exact.bank_statement_lines if b.reference not in paid
            ),
        )
        result = reconcile_all(build_inputs(events, dropped), TOL)
        assert {(b.exception_type, b.reference) for b in result.breaks} == {
            (ExceptionType.CASH_BREAK, dividend_id) for dividend_id in paid
        }
        for b in result.breaks:
            assert (b.impact_amount, b.impact_currency) == (
                paid[b.reference].total_amount,
                paid[b.reference].currency,
            )

    def test_cash_breaks_block_and_late_settlements_do_not(
        self, dataset: tuple[tuple[Event, ...], ExternalRecords, tuple[DefectEntry, ...]]
    ) -> None:
        events, records, _ = dataset
        result = reconcile_all(build_inputs(events, records), TOL)
        days = {b.business_date for b in result.breaks}
        by_type = defaultdict(set)
        for x in classify(result.breaks, POLICY, applied_rates(events, days)):
            by_type[x.exception_type].add(x.severity)
        assert by_type[ExceptionType.CASH_BREAK] == {Severity.CRITICAL}
        assert by_type[ExceptionType.SETTLEMENT_LATE] <= {
            Severity.MEDIUM,
            Severity.HIGH,
            Severity.CRITICAL,
        }
        assert Severity.LOW not in by_type[ExceptionType.SETTLEMENT_LATE]


D = date(2026, 3, 2)


def _exe(execution_id: str = "exe_1", price: str = "10.00") -> InternalExecution:
    return InternalExecution(
        execution_id,
        "a1",
        "i1",
        "BUY",
        Decimal(5),
        Decimal(price),
        Currency.CAD,
        D,
        Decimal("50.00"),
    )


class TestStages:
    def test_a_lost_reference_matches_on_composite_keys_then_tolerance(self) -> None:
        composite = ExternalExecution(
            "clr_1", "garbled", "a1", "i1", "BUY", Decimal(5), Decimal("10.00"), Currency.CAD, D
        )
        result = match_executions([_exe()], [composite], TOL, {"i1": 1})
        assert [m.rule_id for m in result.matches] == ["executions.composite"]
        assert result.breaks == ()
        near = ExternalExecution(
            "clr_2", "garbled", "a1", "i1", "BUY", Decimal(5), Decimal("10.0001"), Currency.CAD, D
        )
        result = match_executions([_exe()], [near], TOL, {"i1": 1})
        assert [m.rule_id for m in result.matches] == ["executions.tolerance"]
        assert result.breaks == ()  # within the price tolerance
        far = ExternalExecution(
            "clr_3", "garbled", "a1", "i1", "BUY", Decimal(5), Decimal("10.0002"), Currency.CAD, D
        )
        result = match_executions([_exe()], [far], TOL, {"i1": 1})
        assert {b.exception_type for b in result.breaks} == {
            ExceptionType.MISSING_EXTERNAL,
            ExceptionType.MISSING_INTERNAL,
        }

    def test_a_price_outside_tolerance_on_a_matched_reference_is_a_mismatch(self) -> None:
        e = ExternalExecution(
            "clr_1", "exe_1", "a1", "i1", "BUY", Decimal(5), Decimal("10.0002"), Currency.CAD, D
        )
        (b,) = match_executions([_exe()], [e], TOL, {"i1": 1}).breaks
        assert (b.exception_type, b.impact_amount, b.impact_basis) == (
            ExceptionType.PRICE_MISMATCH,
            Decimal("0.0002"),
            "price_per_unit",
        )

    def test_an_orphan_confirmation_is_valued_with_its_multiplier(self) -> None:
        e = ExternalExecution(
            "clr_1", "exe_9", "a1", "opt", "BUY", Decimal(2), Decimal("1.50"), Currency.USD, D
        )
        (b,) = match_executions([], [e], TOL, {"opt": 100}).breaks
        assert (b.exception_type, b.impact_amount) == (
            ExceptionType.MISSING_INTERNAL,
            Decimal("300.00"),
        )

    def test_a_shifted_bank_date_is_a_break_unless_the_settlement_was_late(self) -> None:
        ours = [InternalCash("stl_1", Decimal("-100.00"), Currency.CAD, D)]
        theirs = [
            ExternalCash("bnk_1", "stl_1", Decimal("-100.00"), Currency.CAD, date(2026, 3, 4))
        ]
        (b,) = match_cash(ours, theirs, TOL, {}).breaks
        assert b.exception_type is ExceptionType.CASH_BREAK
        result = match_cash(ours, theirs, TOL, {"stl_1": date(2026, 3, 4)})
        assert result.breaks == ()
        assert [m.rule_id for m in result.matches] == ["cash.late_settlement_timing"]

    def test_custody_missing_a_holding_is_a_position_mismatch(self) -> None:
        ours = [InternalPosition("a1", "i1", D, Decimal(10), Currency.CAD)]
        theirs = [ExternalPosition("cus_1", "a2", "i1", D, Decimal(3))]
        breaks = match_positions(ours, theirs, TOL).breaks
        assert {(b.reference, b.impact_amount) for b in breaks} == {
            ("a1:i1:2026-03-02", Decimal(-10)),
            ("a2:i1:2026-03-02", Decimal(3)),
        }

    def test_a_repeated_previous_close_is_stale_and_another_difference_a_mismatch(self) -> None:
        ours = [
            InternalPrice("i1", date(2026, 3, 2), Decimal("10.00"), Currency.CAD),
            InternalPrice("i1", date(2026, 3, 3), Decimal("10.50"), Currency.CAD),
        ]
        stale = [ExternalPrice("pxf_1", "i1", date(2026, 3, 3), Decimal("10.00"), Currency.CAD)]
        (b,) = match_prices(ours, stale, TOL).breaks
        assert (b.exception_type, b.impact_amount) == (ExceptionType.STALE_PRICE, Decimal("0.50"))
        wrong = [ExternalPrice("pxf_2", "i1", date(2026, 3, 3), Decimal("10.40"), Currency.CAD)]
        (b,) = match_prices(ours, wrong, TOL).breaks
        assert b.exception_type is ExceptionType.PRICE_MISMATCH


def test_a_usd_break_without_an_applicable_rate_is_refused() -> None:
    e = ExternalExecution(
        "clr_1", "exe_9", "a1", "i1", "BUY", Decimal(2), Decimal("1.50"), Currency.USD, D
    )
    breaks = match_executions([], [e], TOL, {"i1": 1}).breaks
    with pytest.raises(Exception, match="no USD/CAD rate applies on 2026-03-02"):
        classify(breaks, POLICY, {})
