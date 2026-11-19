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

Long only: selling more than the trade-date quantity raises ``ShortSaleError``.
Average cost is not computed here (Phase 3 positions mart).
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from northstar.common.exceptions import NorthstarError
from northstar.domain.events import Event, Side, TradeExecuted, TradeSettled


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
    """Applies trade and settlement events, in order, to positions and the stock record.

    Other event types do not change positions and are ignored.
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
            ShortSaleError: A SELL exceeds the trade-date quantity.
            SettlementError: A settlement exceeds the quantity in flight.
        """
        if isinstance(event, TradeExecuted):
            self._apply_execution(event)
        elif isinstance(event, TradeSettled):
            self._apply_settlement(event)

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

    def _apply_execution(self, event: TradeExecuted) -> None:
        p = event.payload
        key = (p.account_id, p.instrument_id)
        if p.side is Side.BUY:
            self._trade_date[key] += p.quantity
            self._locations[(p.instrument_id, Location.PENDING_RECEIPT)] += p.quantity
            return
        if p.quantity > self._trade_date[key]:
            msg = (
                f"{p.execution_id}: selling {p.quantity} of {p.instrument_id} but "
                f"{p.account_id} holds {self._trade_date[key]} (short selling is out of scope)"
            )
            raise ShortSaleError(msg)
        self._trade_date[key] -= p.quantity
        self._pending_delivery[p.instrument_id] += p.quantity

    def _apply_settlement(self, event: TradeSettled) -> None:
        p = event.payload
        key = (p.account_id, p.instrument_id)
        if p.side is Side.BUY:
            pending = self._locations[(p.instrument_id, Location.PENDING_RECEIPT)]
            if p.quantity > pending:
                msg = f"{p.settlement_id}: receiving {p.quantity} but only {pending} is pending"
                raise SettlementError(msg)
            self._locations[(p.instrument_id, Location.PENDING_RECEIPT)] -= p.quantity
            self._locations[(p.instrument_id, Location.DEPOSITORY)] += p.quantity
            self._settled[key] += p.quantity
            return
        if p.quantity > self._pending_delivery[p.instrument_id]:
            msg = (
                f"{p.settlement_id}: delivering {p.quantity} but only "
                f"{self._pending_delivery[p.instrument_id]} is pending delivery"
            )
            raise SettlementError(msg)
        self._pending_delivery[p.instrument_id] -= p.quantity
        self._locations[(p.instrument_id, Location.DEPOSITORY)] -= p.quantity
        self._settled[key] -= p.quantity
