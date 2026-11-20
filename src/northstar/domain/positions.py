"""Positions and the stock record, projected from canonical events.

Two views per account x instrument (domain model §9.4):

- **trade-date quantity** changes when a trade executes. Risk uses it.
- **settled quantity** changes when the obligation settles. Custody
  reconciliation uses it.

The **stock record** (INV-04) tracks, per instrument, who owns the shares and
where they are. At every step the two sides are equal:

| Event | Ownership side | Location side |
|---|---|---|
| BUY executes | client +q | clearing: pending receipt +q |
| BUY settles | (no change) | pending receipt -q, depository +q |
| SELL executes | client -q, clearing: pending delivery +q | (no change) |
| SELL settles | pending delivery -q | depository -q |
| option exercised or expired (c contracts) | client -c | depository -c |
| call exercised (s shares) | client +s | clearing: pending receipt +s |
| put exercised (s shares) | client -s, clearing: pending delivery +s | (no change) |
| exercise settles | as a BUY (call) or SELL (put) of s settles | |

An exercised or expired option leaves both the trade-date and the settled
position at once: only settled contracts, held in the depository, can be
exercised or expire (roadmap 5C-b, ADR 013).

Long only: selling more than the trade-date quantity raises ``ShortSaleError``,
and so does exercising a put without the shares to deliver.
Average cost is not computed here (Phase 3 positions mart).
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from northstar.common.exceptions import NorthstarError
from northstar.domain.events import (
    Event,
    ExerciseSettled,
    OptionExercised,
    OptionExpired,
    OptionRight,
    Side,
    TradeExecuted,
    TradeSettled,
)


class ShortSaleError(NorthstarError, ValueError):
    """Raised when a SELL exceeds the position (short selling is out of scope)."""


class SettlementError(NorthstarError, ValueError):
    """Raised when a settlement does not match what its executions put in flight."""


class Location(StrEnum):
    """Where an instrument's units are, on the stock record's location side."""

    DEPOSITORY = "depository"
    PENDING_RECEIPT = "clearing_pending_receipt"


@dataclass(frozen=True, slots=True)
class StockRecordLine:
    """One instrument's stock record. ``balanced`` is INV-04."""

    instrument_id: str
    client_ownership: Decimal
    pending_delivery: Decimal
    depository: Decimal
    pending_receipt: Decimal

    @property
    def ownership_total(self) -> Decimal:
        """Units owned: client positions plus units owed to clearing on sales."""
        return self.client_ownership + self.pending_delivery

    @property
    def location_total(self) -> Decimal:
        """Units located: in the depository plus units due in from clearing."""
        return self.depository + self.pending_receipt

    @property
    def balanced(self) -> bool:
        """Whether ownership equals location (INV-04)."""
        return self.ownership_total == self.location_total


class PositionBook:
    """Applies events, in order, to positions and the stock record.

    Trades, settlements, exercises, and expiries change positions; other event
    types are ignored.
    """

    def __init__(self) -> None:
        """Start with no positions."""
        self._trade_date: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
        self._settled: defaultdict[tuple[str, str], Decimal] = defaultdict(Decimal)
        self._pending_delivery: defaultdict[str, Decimal] = defaultdict(Decimal)
        self._locations: defaultdict[tuple[str, Location], Decimal] = defaultdict(Decimal)

    def apply(self, event: Event) -> None:
        """Apply one event.

        Raises:
            ShortSaleError: A SELL, or a put's delivery, exceeds the trade-date quantity.
            SettlementError: A settlement exceeds the quantity in flight, or an
                exercise or expiry exceeds the settled contracts.
        """
        match event:
            case TradeExecuted(payload=p):
                if p.side is Side.BUY:
                    self._receive(p.account_id, p.instrument_id, p.quantity)
                else:
                    self._deliver(p.account_id, p.instrument_id, p.quantity, p.execution_id)
            case TradeSettled(payload=p):
                self._settle(p.account_id, p.instrument_id, p.side, p.quantity, p.settlement_id)
            case OptionExercised(payload=p):
                self._retire(p.account_id, p.option_instrument_id, p.contracts, p.exercise_id)
                if p.right is OptionRight.CALL:
                    self._receive(p.account_id, p.underlying_instrument_id, p.shares)
                else:
                    self._deliver(p.account_id, p.underlying_instrument_id, p.shares, p.exercise_id)
            case ExerciseSettled(payload=p):
                self._settle(p.account_id, p.instrument_id, p.side, p.quantity, p.settlement_id)
            case OptionExpired(payload=p):
                self._retire(
                    p.account_id, p.instrument_id, p.contracts, f"expiry of {p.instrument_id}"
                )
            case _:
                pass

    def apply_all(self, events: Iterable[Event]) -> "PositionBook":
        """Apply events in order and return ``self``."""
        for event in events:
            self.apply(event)
        return self

    def trade_date_quantity(self, account_id: str, instrument_id: str) -> Decimal:
        """Units the account owns as of the latest applied trade date."""
        return self._trade_date[(account_id, instrument_id)]

    def settled_quantity(self, account_id: str, instrument_id: str) -> Decimal:
        """Units the account owns on a settlement-date basis."""
        return self._settled[(account_id, instrument_id)]

    def positions(self) -> dict[tuple[str, str], tuple[Decimal, Decimal]]:
        """Every non-zero (trade-date, settled) position keyed by (account, instrument)."""
        keys = set(self._trade_date) | set(self._settled)
        return {
            key: (self._trade_date[key], self._settled[key])
            for key in sorted(keys)
            if self._trade_date[key] or self._settled[key]
        }

    def stock_record(self) -> list[StockRecordLine]:
        """One stock record line per instrument that has ever moved."""
        instruments = {i for _, i in self._trade_date} | {i for i, _ in self._locations}
        lines = []
        for instrument_id in sorted(instruments):
            client = sum(
                (q for (_, i), q in self._trade_date.items() if i == instrument_id), Decimal(0)
            )
            lines.append(
                StockRecordLine(
                    instrument_id=instrument_id,
                    client_ownership=client,
                    pending_delivery=self._pending_delivery[instrument_id],
                    depository=self._locations[(instrument_id, Location.DEPOSITORY)],
                    pending_receipt=self._locations[(instrument_id, Location.PENDING_RECEIPT)],
                )
            )
        return lines

    def _receive(self, account_id: str, instrument_id: str, quantity: Decimal) -> None:
        """The client owns ``quantity`` more, due in from clearing."""
        self._trade_date[(account_id, instrument_id)] += quantity
        self._locations[(instrument_id, Location.PENDING_RECEIPT)] += quantity

    def _deliver(self, account_id: str, instrument_id: str, quantity: Decimal, ref: str) -> None:
        """The client owns ``quantity`` less, owed to clearing (long only)."""
        key = (account_id, instrument_id)
        if quantity > self._trade_date[key]:
            msg = (
                f"{ref}: delivering {quantity} of {instrument_id} but {account_id} holds "
                f"{self._trade_date[key]} (short selling is out of scope)"
            )
            raise ShortSaleError(msg)
        self._trade_date[key] -= quantity
        self._pending_delivery[instrument_id] += quantity

    def _settle(
        self, account_id: str, instrument_id: str, side: Side, quantity: Decimal, ref: str
    ) -> None:
        """Units in flight arrive in, or leave, the depository and the settled position."""
        key = (account_id, instrument_id)
        if side is Side.BUY:
            pending = self._locations[(instrument_id, Location.PENDING_RECEIPT)]
            if quantity > pending:
                msg = f"{ref}: receiving {quantity} but only {pending} is pending"
                raise SettlementError(msg)
            self._locations[(instrument_id, Location.PENDING_RECEIPT)] -= quantity
            self._locations[(instrument_id, Location.DEPOSITORY)] += quantity
            self._settled[key] += quantity
            return
        if quantity > self._pending_delivery[instrument_id]:
            msg = (
                f"{ref}: delivering {quantity} but only "
                f"{self._pending_delivery[instrument_id]} is pending delivery"
            )
            raise SettlementError(msg)
        self._pending_delivery[instrument_id] -= quantity
        self._locations[(instrument_id, Location.DEPOSITORY)] -= quantity
        self._settled[key] -= quantity

    def _retire(self, account_id: str, instrument_id: str, contracts: Decimal, ref: str) -> None:
        """Exercised or expired contracts leave the account and the depository at once."""
        key = (account_id, instrument_id)
        owned, settled = self._trade_date[key], self._settled[key]
        if contracts > owned or contracts > settled:
            msg = (
                f"{ref}: retiring {contracts} of {instrument_id} but {account_id} owns {owned} "
                f"with {settled} settled (only settled contracts are exercised or expire)"
            )
            raise SettlementError(msg)
        self._trade_date[key] -= contracts
        self._settled[key] -= contracts
        self._locations[(instrument_id, Location.DEPOSITORY)] -= contracts
