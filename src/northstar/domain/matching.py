"""Staged matching of internal records against external ones (FR-M2-02, FR-M2-03).

Seven reconciliations (domain model §7.1). Each matches in stages:

1. **exact** on the stable ID the counterparty echoes (the broker reference)
2. **composite** on business keys, for external records whose reference
   matches nothing (a counterparty that lost or garbled the reference)
3. **tolerance** on the composite keys with prices within the policy tolerance

Every match records the rule and rule version that produced it. Whatever is
left unmatched, or matched with different values, becomes a ``Break``: the
raw finding, with its impact in the record's own currency. Classification
(severity, CAD impact) happens in ``classify``, so a break's facts never
depend on policy thresholds.

The functions are pure and deterministic: the same inputs always give the
same matches and breaks, in the same order.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from northstar.common.money import Currency
from northstar.domain.reconciliation import ExceptionType, Tolerances

RULE_VERSION = 1
"""Version of every matching rule in this module (bump when a rule's logic changes)."""

CENT = Decimal("0.01")


class Reconciliation(StrEnum):
    """The seven reconciliations (domain model §7.1)."""

    EXECUTIONS = "executions"
    SETTLEMENTS = "settlements"
    CASH = "cash"
    POSITIONS = "positions"
    FEES = "fees"
    FX = "fx"
    PRICES = "prices"


@dataclass(frozen=True, slots=True)
class Match:
    """An internal record and the external record(s) it agrees with."""

    reconciliation: Reconciliation
    rule_id: str
    rule_version: int
    internal_ref: str
    external_record_ids: tuple[str, ...]
    business_date: date


@dataclass(frozen=True, slots=True)
class Break:
    """A difference between the two sides, before classification."""

    reconciliation: Reconciliation
    exception_type: ExceptionType
    reference: str
    business_date: date
    impact_amount: Decimal
    impact_currency: Currency
    impact_basis: Literal["amount", "price_per_unit", "quantity"]
    external_record_ids: tuple[str, ...]
    detail: str


@dataclass(frozen=True, slots=True)
class Result:
    """What one reconciliation found."""

    matches: tuple[Match, ...]
    breaks: tuple[Break, ...]


def _rule(reconciliation: Reconciliation, stage: str) -> str:
    return f"{reconciliation}.{stage}"


# --- Executions -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InternalExecution:
    """An execution as the ledger knows it (``fct_executions`` + its posted gross)."""

    execution_id: str
    account_id: str
    instrument_id: str
    side: str
    quantity: Decimal
    price: Decimal
    currency: Currency
    trade_date: date
    gross_amount: Decimal


@dataclass(frozen=True, slots=True)
class ExternalExecution:
    """A clearing confirmation."""

    record_id: str
    broker_reference: str
    account_id: str
    instrument_id: str
    side: str
    quantity: Decimal
    price: Decimal
    currency: Currency
    trade_date: date


def match_executions(
    internal: Sequence[InternalExecution],
    external: Sequence[ExternalExecution],
    tolerances: Tolerances,
    multipliers: Mapping[str, int],
) -> Result:
    """Executions against clearing confirmations.

    ``multipliers`` (instrument ID to contract multiplier) values a
    confirmation that has no internal execution.

    Raises:
        KeyError: A confirmation names an instrument with no multiplier.
    """
    recon = Reconciliation.EXECUTIONS
    by_ref: dict[str, list[ExternalExecution]] = defaultdict(list)
    for e in external:
        by_ref[e.broker_reference].append(e)
    known = {i.execution_id for i in internal}
    orphans = [e for e in external if e.broker_reference not in known]
    matches: list[Match] = []
    breaks: list[Break] = []
    for i in internal:
        candidates = by_ref.get(i.execution_id, [])
        stage = "exact_id"
        if not candidates:
            found = _composite_execution(i, orphans, tolerances)
            if found is None:
                breaks.append(
                    Break(
                        recon,
                        ExceptionType.MISSING_EXTERNAL,
                        i.execution_id,
                        i.trade_date,
                        i.gross_amount,
                        i.currency,
                        "amount",
                        (),
                        "no clearing confirmation",
                    )
                )
                continue
            stage, chosen = found
            orphans.remove(chosen)
            candidates = [chosen]
        first, *duplicates = candidates
        matches.append(
            Match(
                recon,
                _rule(recon, stage),
                RULE_VERSION,
                i.execution_id,
                (first.record_id,),
                i.trade_date,
            )
        )
        breaks.extend(_execution_field_breaks(i, first, tolerances))
        breaks.extend(
            Break(
                recon,
                ExceptionType.DUPLICATE_EXTERNAL,
                i.execution_id,
                i.trade_date,
                i.gross_amount,
                i.currency,
                "amount",
                (d.record_id,),
                "clearing confirmation received more than once",
            )
            for d in duplicates
        )
    breaks.extend(
        Break(
            recon,
            ExceptionType.MISSING_INTERNAL,
            e.broker_reference,
            e.trade_date,
            (e.quantity * e.price * multipliers[e.instrument_id]).quantize(CENT),
            e.currency,
            "amount",
            (e.record_id,),
            "clearing confirmation for no known execution",
        )
        for e in orphans
    )
    return Result(tuple(matches), tuple(breaks))


def _composite_execution(
    i: InternalExecution, orphans: Sequence[ExternalExecution], tolerances: Tolerances
) -> tuple[str, ExternalExecution] | None:
    def same_keys(e: ExternalExecution) -> bool:
        return (e.account_id, e.instrument_id, e.side, e.trade_date, e.quantity) == (
            i.account_id,
            i.instrument_id,
            i.side,
            i.trade_date,
            i.quantity,
        )

    for e in orphans:
        if same_keys(e) and e.price == i.price:
            return "composite", e
    for e in orphans:
        if same_keys(e) and abs(e.price - i.price) <= tolerances.price_per_unit:
            return "tolerance", e
    return None


def _execution_field_breaks(
    i: InternalExecution, e: ExternalExecution, tolerances: Tolerances
) -> Iterable[Break]:
    recon = Reconciliation.EXECUTIONS
    if abs(e.quantity - i.quantity) > tolerances.quantity:
        yield Break(
            recon,
            ExceptionType.QUANTITY_MISMATCH,
            i.execution_id,
            i.trade_date,
            ((e.quantity - i.quantity) * i.price).quantize(CENT),
            i.currency,
            "amount",
            (e.record_id,),
            f"quantity {i.quantity} internal, {e.quantity} external",
        )
    if abs(e.price - i.price) > tolerances.price_per_unit:
        yield Break(
            recon,
            ExceptionType.PRICE_MISMATCH,
            i.execution_id,
            i.trade_date,
            abs(e.price - i.price),
            i.currency,
            "price_per_unit",
            (e.record_id,),
            f"price {i.price} internal, {e.price} external",
        )


# --- Settlements ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InternalSettlement:
    """A settled obligation (``fct_settlements``, status SETTLED)."""

    settlement_id: str
    account_id: str
    instrument_id: str
    quantity: Decimal
    amount: Decimal
    currency: Currency
    settlement_date: date


@dataclass(frozen=True, slots=True)
class ExternalSettlement:
    """A settlement confirmation."""

    record_id: str
    broker_reference: str
    account_id: str
    instrument_id: str
    quantity: Decimal
    amount: Decimal
    currency: Currency
    contractual_settlement_date: date
    actual_settlement_date: date


def match_settlements(
    internal: Sequence[InternalSettlement],
    external: Sequence[ExternalSettlement],
    tolerances: Tolerances,
) -> Result:
    """Settlement obligations against settlement confirmations.

    A confirmation whose actual date is after the contractual date is
    ``SETTLEMENT_LATE``, with the obligation's amount as its impact.
    """
    recon = Reconciliation.SETTLEMENTS
    by_ref = {e.broker_reference: e for e in external}
    known = {i.settlement_id for i in internal}
    orphans = [e for e in external if e.broker_reference not in known]
    matches: list[Match] = []
    breaks: list[Break] = []
    for i in internal:
        e = by_ref.get(i.settlement_id)
        stage = "exact_id"
        if e is None:
            e = next(
                (
                    o
                    for o in orphans
                    if (o.account_id, o.instrument_id, o.contractual_settlement_date, o.quantity)
                    == (i.account_id, i.instrument_id, i.settlement_date, i.quantity)
                ),
                None,
            )
            stage = "composite"
            if e is None:
                breaks.append(
                    Break(
                        recon,
                        ExceptionType.MISSING_EXTERNAL,
                        i.settlement_id,
                        i.settlement_date,
                        i.amount,
                        i.currency,
                        "amount",
                        (),
                        "no settlement confirmation",
                    )
                )
                continue
            orphans.remove(e)
        matches.append(
            Match(
                recon,
                _rule(recon, stage),
                RULE_VERSION,
                i.settlement_id,
                (e.record_id,),
                i.settlement_date,
            )
        )
        if (
            abs(e.amount - i.amount) > tolerances.amount
            or abs(e.quantity - i.quantity) > tolerances.quantity
        ):
            breaks.append(
                Break(
                    recon,
                    ExceptionType.SETTLEMENT_AMOUNT_MISMATCH,
                    i.settlement_id,
                    i.settlement_date,
                    e.amount - i.amount,
                    i.currency,
                    "amount",
                    (e.record_id,),
                    f"amount {i.amount} internal, {e.amount} external",
                )
            )
        if e.actual_settlement_date > e.contractual_settlement_date:
            breaks.append(
                Break(
                    recon,
                    ExceptionType.SETTLEMENT_LATE,
                    i.settlement_id,
                    i.settlement_date,
                    i.amount,
                    i.currency,
                    "amount",
                    (e.record_id,),
                    f"settled {e.actual_settlement_date}, due {e.contractual_settlement_date}",
                )
            )
    breaks.extend(
        Break(
            recon,
            ExceptionType.MISSING_INTERNAL,
            e.broker_reference,
            e.contractual_settlement_date,
            e.amount,
            e.currency,
            "amount",
            (e.record_id,),
            "settlement confirmation for no known obligation",
        )
        for e in orphans
    )
    return Result(tuple(matches), tuple(breaks))


# --- Cash -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InternalCash:
    """A movement on the firm's bank account (GL 1000), signed from the bank's view."""

    reference: str
    amount: Decimal
    currency: Currency
    value_date: date


@dataclass(frozen=True, slots=True)
class ExternalCash:
    """A bank statement line."""

    record_id: str
    reference: str
    amount: Decimal
    currency: Currency
    value_date: date


def match_cash(
    internal: Sequence[InternalCash],
    external: Sequence[ExternalCash],
    tolerances: Tolerances,
    late_settlements: Mapping[str, date],
) -> Result:
    """GL cash movements against bank statement lines.

    ``late_settlements`` maps a settlement reference to the date it actually
    settled. A bank line for such a reference that arrives on that date is an
    explained timing difference: matched (rule ``cash.late_settlement_timing``),
    because the settlement reconciliation already reports it as
    ``SETTLEMENT_LATE``. Any other unmatched cash is a ``CASH_BREAK``.
    """
    recon = Reconciliation.CASH
    by_ref = {e.reference: e for e in external}
    known = {i.reference for i in internal}
    orphans = [e for e in external if e.reference not in known]
    matches: list[Match] = []
    breaks: list[Break] = []
    for i in internal:
        e = by_ref.get(i.reference)
        stage = "exact_reference"
        if e is None:
            e = next(
                (
                    o
                    for o in orphans
                    if (o.value_date, o.amount, o.currency) == (i.value_date, i.amount, i.currency)
                ),
                None,
            )
            stage = "composite"
            if e is None:
                breaks.append(
                    Break(
                        recon,
                        ExceptionType.CASH_BREAK,
                        i.reference,
                        i.value_date,
                        abs(i.amount),
                        i.currency,
                        "amount",
                        (),
                        "no bank statement line",
                    )
                )
                continue
            orphans.remove(e)
        amount_differs = abs(e.amount - i.amount) > tolerances.amount or e.currency != i.currency
        if amount_differs:
            breaks.append(
                Break(
                    recon,
                    ExceptionType.CASH_BREAK,
                    i.reference,
                    i.value_date,
                    abs(e.amount - i.amount),
                    i.currency,
                    "amount",
                    (e.record_id,),
                    f"amount {i.amount} internal, {e.amount} external",
                )
            )
            continue
        if e.value_date != i.value_date:
            if late_settlements.get(i.reference) == e.value_date:
                stage = "late_settlement_timing"
            else:
                breaks.append(
                    Break(
                        recon,
                        ExceptionType.CASH_BREAK,
                        i.reference,
                        i.value_date,
                        abs(i.amount),
                        i.currency,
                        "amount",
                        (e.record_id,),
                        f"value date {i.value_date} internal, {e.value_date} external",
                    )
                )
                continue
        matches.append(
            Match(
                recon, _rule(recon, stage), RULE_VERSION, i.reference, (e.record_id,), i.value_date
            )
        )
    breaks.extend(
        Break(
            recon,
            ExceptionType.CASH_BREAK,
            e.reference,
            e.value_date,
            abs(e.amount),
            e.currency,
            "amount",
            (e.record_id,),
            "bank line with no ledger movement",
        )
        for e in orphans
    )
    return Result(tuple(matches), tuple(breaks))


# --- Positions ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InternalPosition:
    """A settled position (``fct_positions_daily.settled_quantity``)."""

    account_id: str
    instrument_id: str
    business_date: date
    settled_quantity: Decimal
    currency: Currency


@dataclass(frozen=True, slots=True)
class ExternalPosition:
    """A custody snapshot line."""

    record_id: str
    account_id: str
    instrument_id: str
    snapshot_date: date
    settled_quantity: Decimal


def match_positions(
    internal: Sequence[InternalPosition],
    external: Sequence[ExternalPosition],
    tolerances: Tolerances,
) -> Result:
    """Settled positions against custody, on the dates custody reports.

    Custody lists only positive holdings, so a missing line means zero. Only
    dates that appear in the custody snapshots are compared.
    """
    recon = Reconciliation.POSITIONS
    dates = {e.snapshot_date for e in external}
    ours = {
        (i.account_id, i.instrument_id, i.business_date): i
        for i in internal
        if i.business_date in dates
    }
    currencies = {(i.account_id, i.instrument_id): i.currency for i in internal}
    theirs = {(e.account_id, e.instrument_id, e.snapshot_date): e for e in external}
    matches: list[Match] = []
    breaks: list[Break] = []
    for key in sorted(set(ours) | set(theirs)):
        account_id, instrument_id, day = key
        mine = ours.get(key)
        quantity = mine.settled_quantity if mine is not None else Decimal(0)
        e = theirs.get(key)
        custody = e.settled_quantity if e is not None else Decimal(0)
        ref = f"{account_id}:{instrument_id}:{day.isoformat()}"
        ids = (e.record_id,) if e is not None else ()
        if abs(custody - quantity) > tolerances.quantity:
            breaks.append(
                Break(
                    recon,
                    ExceptionType.POSITION_MISMATCH,
                    ref,
                    day,
                    custody - quantity,
                    currencies.get((account_id, instrument_id), Currency.CAD),
                    "quantity",
                    ids,
                    f"settled {quantity} internal, {custody} at custody (units)",
                )
            )
        elif e is not None:
            matches.append(Match(recon, _rule(recon, "composite"), RULE_VERSION, ref, ids, day))
    return Result(tuple(matches), tuple(breaks))


# --- Fees -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InternalFee:
    """A clearing fee the ledger posted for an execution (GL 5100)."""

    execution_id: str
    account_id: str
    amount: Decimal
    currency: Currency
    trade_date: date


@dataclass(frozen=True, slots=True)
class ExternalFee:
    """An external fee record."""

    record_id: str
    broker_reference: str
    account_id: str
    amount: Decimal
    currency: Currency
    trade_date: date


def match_fees(
    internal: Sequence[InternalFee], external: Sequence[ExternalFee], tolerances: Tolerances
) -> Result:
    """Posted clearing fees against the clearing firm's fee records."""
    recon = Reconciliation.FEES
    by_ref = {e.broker_reference: e for e in external}
    known = {i.execution_id for i in internal}
    orphans = [e for e in external if e.broker_reference not in known]
    matches: list[Match] = []
    breaks: list[Break] = []
    for i in internal:
        e = by_ref.get(i.execution_id)
        stage = "exact_id"
        if e is None:
            e = next(
                (
                    o
                    for o in orphans
                    if (o.account_id, o.trade_date, o.amount)
                    == (i.account_id, i.trade_date, i.amount)
                ),
                None,
            )
            stage = "composite"
            if e is None:
                breaks.append(
                    Break(
                        recon,
                        ExceptionType.MISSING_EXTERNAL,
                        i.execution_id,
                        i.trade_date,
                        i.amount,
                        i.currency,
                        "amount",
                        (),
                        "no fee record",
                    )
                )
                continue
            orphans.remove(e)
        if abs(e.amount - i.amount) > tolerances.amount:
            breaks.append(
                Break(
                    recon,
                    ExceptionType.FEE_MISMATCH,
                    i.execution_id,
                    i.trade_date,
                    e.amount - i.amount,
                    i.currency,
                    "amount",
                    (e.record_id,),
                    f"fee {i.amount} posted, {e.amount} charged",
                )
            )
        else:
            matches.append(
                Match(
                    recon,
                    _rule(recon, stage),
                    RULE_VERSION,
                    i.execution_id,
                    (e.record_id,),
                    i.trade_date,
                )
            )
    breaks.extend(
        Break(
            recon,
            ExceptionType.MISSING_INTERNAL,
            e.broker_reference,
            e.trade_date,
            e.amount,
            e.currency,
            "amount",
            (e.record_id,),
            "fee record for no posted fee",
        )
        for e in orphans
    )
    return Result(tuple(matches), tuple(breaks))


# --- FX -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InternalFx:
    """A client conversion (``fct_cash_movements``, FX_CONVERSION)."""

    fx_conversion_id: str
    account_id: str
    sell_amount: Decimal
    sell_currency: Currency
    buy_amount: Decimal
    buy_currency: Currency
    trade_date: date


@dataclass(frozen=True, slots=True)
class ExternalFx:
    """An FX confirmation."""

    record_id: str
    broker_reference: str
    account_id: str
    sell_amount: Decimal
    sell_currency: Currency
    buy_amount: Decimal
    buy_currency: Currency
    trade_date: date


def match_fx(
    internal: Sequence[InternalFx], external: Sequence[ExternalFx], tolerances: Tolerances
) -> Result:
    """Client conversions against FX confirmations. Impact is in the bought currency."""
    recon = Reconciliation.FX
    by_ref = {e.broker_reference: e for e in external}
    matches: list[Match] = []
    breaks: list[Break] = []
    for i in internal:
        e = by_ref.pop(i.fx_conversion_id, None)
        if e is None:
            breaks.append(
                Break(
                    recon,
                    ExceptionType.MISSING_EXTERNAL,
                    i.fx_conversion_id,
                    i.trade_date,
                    i.buy_amount,
                    i.buy_currency,
                    "amount",
                    (),
                    "no FX confirmation",
                )
            )
            continue
        legs_differ = (
            abs(e.sell_amount - i.sell_amount) > tolerances.amount
            or abs(e.buy_amount - i.buy_amount) > tolerances.amount
            or (e.sell_currency, e.buy_currency) != (i.sell_currency, i.buy_currency)
        )
        if legs_differ:
            breaks.append(
                Break(
                    recon,
                    ExceptionType.FX_MISMATCH,
                    i.fx_conversion_id,
                    i.trade_date,
                    i.buy_amount - e.buy_amount,
                    i.buy_currency,
                    "amount",
                    (e.record_id,),
                    f"bought {i.buy_amount} internal, {e.buy_amount} confirmed",
                )
            )
        else:
            matches.append(
                Match(
                    recon,
                    _rule(recon, "exact_id"),
                    RULE_VERSION,
                    i.fx_conversion_id,
                    (e.record_id,),
                    i.trade_date,
                )
            )
    breaks.extend(
        Break(
            recon,
            ExceptionType.MISSING_INTERNAL,
            e.broker_reference,
            e.trade_date,
            e.buy_amount,
            e.buy_currency,
            "amount",
            (e.record_id,),
            "FX confirmation for no known conversion",
        )
        for e in by_ref.values()
    )
    return Result(tuple(matches), tuple(breaks))


# --- Prices ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InternalPrice:
    """A daily close (``price_updated``)."""

    instrument_id: str
    price_date: date
    close_price: Decimal
    currency: Currency


@dataclass(frozen=True, slots=True)
class ExternalPrice:
    """A vendor close."""

    record_id: str
    instrument_id: str
    price_date: date
    close_price: Decimal
    currency: Currency


def match_prices(
    internal: Sequence[InternalPrice], external: Sequence[ExternalPrice], tolerances: Tolerances
) -> Result:
    """Our closes against the vendor's, per instrument and date.

    A vendor close that differs from ours but equals our previous close for
    that instrument is ``STALE_PRICE`` (the vendor did not update); any other
    difference beyond the tolerance is ``PRICE_MISMATCH``. Impact is per unit.
    """
    recon = Reconciliation.PRICES
    ours = {(i.instrument_id, i.price_date): i for i in internal}
    previous: dict[tuple[str, date], Decimal] = {}
    last: dict[str, Decimal] = {}
    for i in sorted(internal, key=lambda p: (p.instrument_id, p.price_date)):
        if i.instrument_id in last:
            previous[(i.instrument_id, i.price_date)] = last[i.instrument_id]
        last[i.instrument_id] = i.close_price
    matches: list[Match] = []
    breaks: list[Break] = []
    for e in external:
        key = (e.instrument_id, e.price_date)
        ref = f"{e.instrument_id}:{e.price_date.isoformat()}"
        ours_close = ours.get(key)
        if ours_close is None:
            breaks.append(
                Break(
                    recon,
                    ExceptionType.MISSING_INTERNAL,
                    ref,
                    e.price_date,
                    Decimal(0),
                    e.currency,
                    "price_per_unit",
                    (e.record_id,),
                    "vendor close we don't have",
                )
            )
            continue
        difference = abs(e.close_price - ours_close.close_price)
        if difference <= tolerances.price_per_unit:
            matches.append(
                Match(
                    recon,
                    _rule(recon, "composite"),
                    RULE_VERSION,
                    ref,
                    (e.record_id,),
                    e.price_date,
                )
            )
            continue
        stale = previous.get(key) == e.close_price
        breaks.append(
            Break(
                recon,
                ExceptionType.STALE_PRICE if stale else ExceptionType.PRICE_MISMATCH,
                ref,
                e.price_date,
                difference,
                ours_close.currency,
                "price_per_unit",
                (e.record_id,),
                f"close {ours_close.close_price} ours, {e.close_price} vendor"
                + (" (the previous close)" if stale else ""),
            )
        )
    return Result(tuple(matches), tuple(breaks))


# --- All seven ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Inputs:
    """Both sides of every reconciliation."""

    executions: Sequence[InternalExecution]
    clearing: Sequence[ExternalExecution]
    multipliers: Mapping[str, int]
    settlements: Sequence[InternalSettlement]
    settlement_confirmations: Sequence[ExternalSettlement]
    cash: Sequence[InternalCash]
    bank: Sequence[ExternalCash]
    positions: Sequence[InternalPosition]
    custody: Sequence[ExternalPosition]
    fees: Sequence[InternalFee]
    fee_records: Sequence[ExternalFee]
    fx: Sequence[InternalFx]
    fx_confirmations: Sequence[ExternalFx]
    prices: Sequence[InternalPrice]
    price_feed: Sequence[ExternalPrice]


def reconcile_all(inputs: Inputs, tolerances: Tolerances) -> Result:
    """Run the seven reconciliations; return every match and break.

    Settlements run before cash: a late settlement's bank line, which arrives
    on the actual settlement date, is matched as a timing difference, and its
    record ID is added to the ``SETTLEMENT_LATE`` break, so one exception
    covers both the confirmation and the bank line.
    """
    settlements = match_settlements(inputs.settlements, inputs.settlement_confirmations, tolerances)
    by_reference = {e.broker_reference: e for e in inputs.settlement_confirmations}
    late = {
        b.reference: by_reference[b.reference].actual_settlement_date
        for b in settlements.breaks
        if b.exception_type is ExceptionType.SETTLEMENT_LATE
    }
    cash = match_cash(inputs.cash, inputs.bank, tolerances, late)
    timing = {
        m.internal_ref: m.external_record_ids
        for m in cash.matches
        if m.rule_id == _rule(Reconciliation.CASH, "late_settlement_timing")
    }
    settlement_breaks = tuple(
        Break(
            b.reconciliation,
            b.exception_type,
            b.reference,
            b.business_date,
            b.impact_amount,
            b.impact_currency,
            b.impact_basis,
            b.external_record_ids + timing.get(b.reference, ()),
            b.detail,
        )
        if b.exception_type is ExceptionType.SETTLEMENT_LATE
        else b
        for b in settlements.breaks
    )
    results = (
        match_executions(inputs.executions, inputs.clearing, tolerances, inputs.multipliers),
        Result(settlements.matches, settlement_breaks),
        cash,
        match_positions(inputs.positions, inputs.custody, tolerances),
        match_fees(inputs.fees, inputs.fee_records, tolerances),
        match_fx(inputs.fx, inputs.fx_confirmations, tolerances),
        match_prices(inputs.prices, inputs.price_feed, tolerances),
    )
    return Result(
        tuple(m for r in results for m in r.matches),
        tuple(b for r in results for b in r.breaks),
    )
