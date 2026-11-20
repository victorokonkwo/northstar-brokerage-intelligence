"""Double-entry ledger: chart of accounts, journals, and posting rules.

The ledger is the system of record (ADR 002). Every posting rule from domain
model §9.2 is a pure function of one event, so posting the same event under
the same rule version always produces the same batch, with the same IDs
(domain model §4.1). A ``JournalBatch`` cannot be constructed unless it
balances in every currency (INV-01).
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from northstar.common.exceptions import PolicyNotFoundError, UnbalancedJournalError
from northstar.common.ids import derived_id
from northstar.common.money import Currency, Money
from northstar.domain.calendar import MarketCalendar, business_date
from northstar.domain.events import (
    CashDeposited,
    CashWithdrawn,
    DividendPaid,
    Event,
    EventType,
    ExerciseSettled,
    FxConverted,
    MarginInterestAccrued,
    OptionExercised,
    OptionRight,
    Side,
    TradeExecuted,
    TradeSettled,
)
from northstar.domain.fx import reference_value_cad, spread_revenue
from northstar.domain.trading import gross_amount


class GlAccountType(StrEnum):
    """General-ledger account classes (domain model §9.1)."""

    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    CLEARING = "CLEARING"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"


class GlAccount(BaseModel):
    """One general-ledger account.

    ``per_client`` accounts (2100 Client cash) carry a sub-ledger keyed by the
    client ``account_id``. Every line on them must name the client account.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(pattern=r"^\d{4}$")
    name: str
    type: GlAccountType
    per_client: bool = False


class ChartOfAccounts(BaseModel):
    """Versioned chart of accounts (policy; illustrative v1).

    Raises:
        pydantic.ValidationError: On construction, if a code appears twice.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    effective_from: date
    illustrative: bool
    accounts: tuple[GlAccount, ...]

    @model_validator(mode="after")
    def _unique_codes(self) -> Self:
        codes = [a.code for a in self.accounts]
        if len(codes) != len(set(codes)):
            msg = "account codes must be unique"
            raise ValueError(msg)
        return self

    def account(self, code: str) -> GlAccount:
        """Return the account with ``code``.

        Raises:
            PolicyNotFoundError: The chart has no such account.
        """
        for gl_account in self.accounts:
            if gl_account.code == code:
                return gl_account
        msg = f"chart of accounts {self.version} has no account {code}"
        raise PolicyNotFoundError(msg)


class EntrySide(StrEnum):
    """Debit or credit."""

    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


@dataclass(frozen=True, slots=True)
class JournalLine:
    """One line of a journal batch: a positive amount on one side of one account.

    Raises:
        ValueError: ``amount`` is not positive or not quantized to minor units.
    """

    journal_line_id: UUID
    journal_batch_id: UUID
    line_number: int
    gl_account: str
    client_account_id: str | None
    side: EntrySide
    amount: Money

    def __post_init__(self) -> None:
        """Validate the amount (see the class docstring)."""
        if self.amount.amount <= 0:
            msg = f"journal line amounts must be positive, got {self.amount.amount}"
            raise ValueError(msg)
        if not self.amount.is_quantized:
            msg = f"journal line amounts must be in minor units, got {self.amount.amount}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class JournalBatch:
    """All lines posted for one source event under one posting rule version.

    Raises:
        ValueError: Fewer than two lines, or line numbers not 1..n.
        UnbalancedJournalError: Debits and credits differ in any currency (INV-01).
    """

    journal_batch_id: UUID
    source_event_id: str
    source_event_type: EventType
    posting_rule_id: str
    posting_rule_version: int
    chart_version: str
    ledger_calendar_version: str
    business_date: date
    posting_date: date
    lines: tuple[JournalLine, ...]

    def __post_init__(self) -> None:
        """Validate structure and balance (see the class docstring)."""
        if len(self.lines) < 2:
            msg = f"a journal batch needs at least two lines, got {len(self.lines)}"
            raise ValueError(msg)
        if [line.line_number for line in self.lines] != list(range(1, len(self.lines) + 1)):
            msg = "line numbers must run 1..n in order"
            raise ValueError(msg)
        for currency, (debits, credits) in self.totals().items():
            if debits != credits:
                msg = (
                    f"batch {self.journal_batch_id} from {self.source_event_id} is unbalanced "
                    f"in {currency}: debits {debits.amount}, credits {credits.amount}"
                )
                raise UnbalancedJournalError(msg)

    def totals(self) -> dict[Currency, tuple[Money, Money]]:
        """Return (total debits, total credits) per currency."""
        sums: dict[Currency, dict[EntrySide, Decimal]] = defaultdict(
            lambda: {EntrySide.DEBIT: Decimal(0), EntrySide.CREDIT: Decimal(0)}
        )
        for line in self.lines:
            sums[line.amount.currency][line.side] += line.amount.amount
        return {
            currency: (
                Money(sides[EntrySide.DEBIT], currency),
                Money(sides[EntrySide.CREDIT], currency),
            )
            for currency, sides in sums.items()
        }


@dataclass(frozen=True, slots=True)
class PostingRule:
    """Identity of a posting rule. Changing what a rule posts means a new version."""

    rule_id: str
    version: int


CASH_DEPOSIT: Final = PostingRule("cash_deposited", 1)
TRADE_BUY: Final = PostingRule("trade_executed.buy", 1)
TRADE_SELL: Final = PostingRule("trade_executed.sell", 1)
SETTLE_BUY: Final = PostingRule("trade_settled.buy", 1)
SETTLE_SELL: Final = PostingRule("trade_settled.sell", 1)
CASH_WITHDRAWAL: Final = PostingRule("cash_withdrawn", 1)
FX_BUY_USD: Final = PostingRule("fx_converted.buy_usd", 1)
FX_BUY_CAD: Final = PostingRule("fx_converted.buy_cad", 1)
MARGIN_INTEREST: Final = PostingRule("margin_interest_accrued", 1)
DIVIDEND_PAYMENT: Final = PostingRule("dividend_paid", 1)
EXERCISE_CALL: Final = PostingRule("option_exercised.call", 1)
EXERCISE_PUT: Final = PostingRule("option_exercised.put", 1)
SETTLE_EXERCISE_CALL: Final = PostingRule("exercise_settled.call", 1)
SETTLE_EXERCISE_PUT: Final = PostingRule("exercise_settled.put", 1)

CASH: Final = "1000"
RECEIVABLE_FROM_CLEARING: Final = "1200"
CLIENT_CASH: Final = "2100"
PAYABLE_TO_CLEARING: Final = "2200"
FX_CLEARING: Final = "2300"
COMMISSION_REVENUE: Final = "4100"
FX_REVENUE: Final = "4200"
MARGIN_INTEREST_REVENUE: Final = "4300"
CLEARING_COSTS: Final = "5100"
_RULE_ACCOUNTS: Final = (
    CASH,
    RECEIVABLE_FROM_CLEARING,
    CLIENT_CASH,
    PAYABLE_TO_CLEARING,
    FX_CLEARING,
    COMMISSION_REVENUE,
    FX_REVENUE,
    MARGIN_INTEREST_REVENUE,
    CLEARING_COSTS,
)

NON_POSTING_EVENTS: Final = frozenset(
    {
        EventType.ACCOUNT_CREATED,
        EventType.ACCOUNT_UPDATED,
        EventType.FX_RATE_PUBLISHED,
        EventType.INSTRUMENT_LISTED,
        EventType.ORDER_PLACED,
        EventType.ORDER_CANCELLED,
        EventType.PRICE_UPDATED,
        EventType.DIVIDEND_DECLARED,
        EventType.OPTION_EXPIRED,
    }
)
"""Event types that change no balance and therefore post nothing."""

PostingEvent = (
    CashDeposited
    | CashWithdrawn
    | FxConverted
    | TradeExecuted
    | TradeSettled
    | MarginInterestAccrued
    | DividendPaid
    | OptionExercised
    | ExerciseSettled
)

type _LineSpec = tuple[str, str | None, EntrySide, Money]


class PostingEngine:
    """Posts canonical events to the ledger under the v1 rules (domain model §9.2).

    ``posting_date`` is the event's business date if it is a business day on
    the ledger calendar, otherwise the next business day (domain model §5).
    The ledger calendar is the XTSE CAD settlement calendar.

    Raises:
        PolicyNotFoundError: On construction, if the chart lacks an account
            that a posting rule uses.
    """

    def __init__(self, chart: ChartOfAccounts, ledger_calendar: MarketCalendar) -> None:
        """Bind the engine to a chart of accounts and a ledger calendar."""
        for code in _RULE_ACCOUNTS:
            chart.account(code)
        self._chart = chart
        self._calendar = ledger_calendar

    def post(self, event: PostingEvent) -> JournalBatch:
        """Return the balanced journal batch for ``event``.

        Raises:
            UnbalancedJournalError: The rule produced unbalanced lines (a bug).
            CalendarCoverageError: The posting date is outside the ledger
                calendar's coverage.
        """
        match event:
            case CashDeposited():
                return self._post_deposit(event)
            case CashWithdrawn():
                return self._post_withdrawal(event)
            case FxConverted():
                return self._post_fx(event)
            case TradeExecuted():
                return self._post_execution(event)
            case TradeSettled():
                return self._post_settlement(event)
            case MarginInterestAccrued():
                return self._post_interest(event)
            case DividendPaid():
                return self._post_dividend(event)
            case OptionExercised():
                return self._post_exercise(event)
            case ExerciseSettled():
                return self._post_exercise_settlement(event)

    def post_events(self, events: Iterable[Event]) -> tuple[JournalBatch, ...]:
        """Post every event that changes a balance, in order.

        Events in ``NON_POSTING_EVENTS`` are skipped by design.

        Raises:
            ValueError: An event type has no posting rule and is not declared
                non-posting, so it cannot be silently ignored.
        """
        batches = []
        for event in events:
            if isinstance(event, PostingEvent):
                batches.append(self.post(event))
            elif event.event_type not in NON_POSTING_EVENTS:
                msg = f"no posting rule for event type {event.event_type}"
                raise ValueError(msg)
        return tuple(batches)

    def _post_deposit(self, event: CashDeposited) -> JournalBatch:
        p = event.payload
        amount = Money(p.amount, p.currency)
        return self._batch(
            event,
            CASH_DEPOSIT,
            [
                (CASH, None, EntrySide.DEBIT, amount),
                (CLIENT_CASH, p.account_id, EntrySide.CREDIT, amount),
            ],
        )

    def _post_withdrawal(self, event: CashWithdrawn) -> JournalBatch:
        p = event.payload
        amount = Money(p.amount, p.currency)
        return self._batch(
            event,
            CASH_WITHDRAWAL,
            [
                (CLIENT_CASH, p.account_id, EntrySide.DEBIT, amount),
                (CASH, None, EntrySide.CREDIT, amount),
            ],
        )

    def _post_fx(self, event: FxConverted) -> JournalBatch:
        """OQ-01: spread revenue at conversion against the reference rate.

        The client's CAD side splits into its value at the reference rate
        (2300, the firm's FX position) and the spread (4200). The USD side moves
        between the client and 2300 at face value. Each currency balances.
        """
        p = event.payload
        sell = Money(p.sell_amount, p.sell_currency)
        buy = Money(p.buy_amount, p.buy_currency)
        ref_value = reference_value_cad(sell, buy, p.reference_rate)
        revenue = spread_revenue(sell, buy, p.reference_rate)
        if buy.currency is Currency.USD:
            return self._batch(
                event,
                FX_BUY_USD,
                [
                    (CLIENT_CASH, p.account_id, EntrySide.DEBIT, sell),
                    (FX_CLEARING, None, EntrySide.CREDIT, ref_value),
                    (FX_REVENUE, None, EntrySide.CREDIT, revenue),
                    (FX_CLEARING, None, EntrySide.DEBIT, buy),
                    (CLIENT_CASH, p.account_id, EntrySide.CREDIT, buy),
                ],
            )
        return self._batch(
            event,
            FX_BUY_CAD,
            [
                (CLIENT_CASH, p.account_id, EntrySide.DEBIT, sell),
                (FX_CLEARING, None, EntrySide.CREDIT, sell),
                (FX_CLEARING, None, EntrySide.DEBIT, ref_value),
                (CLIENT_CASH, p.account_id, EntrySide.CREDIT, buy),
                (FX_REVENUE, None, EntrySide.CREDIT, revenue),
            ],
        )

    def _post_interest(self, event: MarginInterestAccrued) -> JournalBatch:
        p = event.payload
        amount = Money(p.amount, p.currency)
        return self._batch(
            event,
            MARGIN_INTEREST,
            [
                (CLIENT_CASH, p.account_id, EntrySide.DEBIT, amount),
                (MARGIN_INTEREST_REVENUE, None, EntrySide.CREDIT, amount),
            ],
        )

    def _post_execution(self, event: TradeExecuted) -> JournalBatch:
        p = event.payload
        gross = gross_amount(p.quantity, p.price, p.multiplier, p.currency)
        commission = Money(p.commission, p.currency)
        specs: list[_LineSpec]
        if p.side is Side.BUY:
            rule = TRADE_BUY
            specs = [
                (CLIENT_CASH, p.account_id, EntrySide.DEBIT, gross + commission),
                (PAYABLE_TO_CLEARING, None, EntrySide.CREDIT, gross),
                (COMMISSION_REVENUE, None, EntrySide.CREDIT, commission),
            ]
        else:
            rule = TRADE_SELL
            net_to_client = gross - commission
            specs = [(RECEIVABLE_FROM_CLEARING, None, EntrySide.DEBIT, gross)]
            if net_to_client.amount < 0:
                # The commission exceeds the proceeds: the client pays the difference.
                specs.append((CLIENT_CASH, p.account_id, EntrySide.DEBIT, -net_to_client))
            else:
                specs.append((CLIENT_CASH, p.account_id, EntrySide.CREDIT, net_to_client))
            specs.append((COMMISSION_REVENUE, None, EntrySide.CREDIT, commission))
        # The clearing firm's fee is a firm expense owed to clearing. These
        # lines come last, so an execution without a fee posts exactly as before.
        clearing_fee = Money(p.clearing_fee, p.currency)
        specs.append((CLEARING_COSTS, None, EntrySide.DEBIT, clearing_fee))
        specs.append((PAYABLE_TO_CLEARING, None, EntrySide.CREDIT, clearing_fee))
        return self._batch(event, rule, specs)

    def _post_settlement(self, event: TradeSettled) -> JournalBatch:
        p = event.payload
        amount = Money(p.amount, p.currency)
        if p.side is Side.BUY:
            return self._batch(
                event,
                SETTLE_BUY,
                [
                    (PAYABLE_TO_CLEARING, None, EntrySide.DEBIT, amount),
                    (CASH, None, EntrySide.CREDIT, amount),
                ],
            )
        return self._batch(
            event,
            SETTLE_SELL,
            [
                (CASH, None, EntrySide.DEBIT, amount),
                (RECEIVABLE_FROM_CLEARING, None, EntrySide.CREDIT, amount),
            ],
        )

    def _post_dividend(self, event: DividendPaid) -> JournalBatch:
        """Post a dividend on its pay date, cash basis (ADR 013).

        The depository pays the total into the bank, and each entitled account's
        client cash is credited its allocation (exact: whole-cent rate x whole shares).
        """
        p = event.payload
        return self._batch(
            event,
            DIVIDEND_PAYMENT,
            [
                (CASH, None, EntrySide.DEBIT, Money(p.total_amount, p.currency)),
                *(
                    (CLIENT_CASH, a.account_id, EntrySide.CREDIT, Money(a.amount, p.currency))
                    for a in p.allocations
                ),
            ],
        )

    def _post_exercise(self, event: OptionExercised) -> JournalBatch:
        """Post an exercise on its exercise date, like a trade at the strike (ADR 013).

        A call: the client pays the strike amount, owed to clearing until the
        underlying is delivered. A put: the client is owed the strike amount,
        receivable from clearing. No fee.
        """
        p = event.payload
        amount = Money(p.strike_amount, p.currency)
        if p.right is OptionRight.CALL:
            return self._batch(
                event,
                EXERCISE_CALL,
                [
                    (CLIENT_CASH, p.account_id, EntrySide.DEBIT, amount),
                    (PAYABLE_TO_CLEARING, None, EntrySide.CREDIT, amount),
                ],
            )
        return self._batch(
            event,
            EXERCISE_PUT,
            [
                (RECEIVABLE_FROM_CLEARING, None, EntrySide.DEBIT, amount),
                (CLIENT_CASH, p.account_id, EntrySide.CREDIT, amount),
            ],
        )

    def _post_exercise_settlement(self, event: ExerciseSettled) -> JournalBatch:
        """Post an exercise's settlement: the strike amount moves through the bank."""
        p = event.payload
        amount = Money(p.amount, p.currency)
        if p.side is Side.BUY:
            return self._batch(
                event,
                SETTLE_EXERCISE_CALL,
                [
                    (PAYABLE_TO_CLEARING, None, EntrySide.DEBIT, amount),
                    (CASH, None, EntrySide.CREDIT, amount),
                ],
            )
        return self._batch(
            event,
            SETTLE_EXERCISE_PUT,
            [
                (CASH, None, EntrySide.DEBIT, amount),
                (RECEIVABLE_FROM_CLEARING, None, EntrySide.CREDIT, amount),
            ],
        )

    def _batch(
        self, event: PostingEvent, rule: PostingRule, specs: list[_LineSpec]
    ) -> JournalBatch:
        batch_id = derived_id("journal_batch", event.event_id, rule.rule_id, rule.version)
        kept = [spec for spec in specs if spec[3].amount != 0]  # e.g. a waived commission
        lines = []
        for line_number, (code, client_account_id, side, amount) in enumerate(kept, start=1):
            if self._chart.account(code).per_client != (client_account_id is not None):
                msg = f"account {code} sub-ledger mismatch in rule {rule.rule_id}"
                raise ValueError(msg)
            lines.append(
                JournalLine(
                    journal_line_id=derived_id(
                        event.event_id, rule.rule_id, rule.version, line_number
                    ),
                    journal_batch_id=batch_id,
                    line_number=line_number,
                    gl_account=code,
                    client_account_id=client_account_id,
                    side=side,
                    amount=amount,
                )
            )
        event_business_date = business_date(event.event_timestamp)
        return JournalBatch(
            journal_batch_id=batch_id,
            source_event_id=event.event_id,
            source_event_type=event.event_type,
            posting_rule_id=rule.rule_id,
            posting_rule_version=rule.version,
            chart_version=self._chart.version,
            ledger_calendar_version=self._calendar.version,
            business_date=event_business_date,
            posting_date=self._calendar.settlement_day_on_or_after(event_business_date),
            lines=tuple(lines),
        )
