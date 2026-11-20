"""The exception lifecycle (domain model §7.4, FR-M2-05, FR-M2-06).

    OPEN → INVESTIGATING → RESOLVED
    OPEN → RESOLVED                (a rule only)
    OPEN → ESCALATED               (SLA breached)
    INVESTIGATING → ESCALATED → RESOLVED

Every change is an append-only transition with an actor, a reason, and a
time. ``RESOLVED`` is terminal. Resolution needs a type, and each type has its
own proof:

- ``AUTO_RULE``: a named, versioned rule (actor ``rule:<id>@<version>``)
- ``MANUAL_OVERRIDE``: a person (actor ``user:<name>``) and a reason
- ``SOURCE_CORRECTED``: the rematch rule, after a rerun no longer finds the break

Rules decide; this module only checks that a requested transition is legal,
and evaluates the rules that propose transitions.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

from northstar.common.exceptions import NorthstarError
from northstar.domain.calendar import MarketCalendar
from northstar.domain.reconciliation import ExceptionType, ReconciliationPolicy, Severity


class Status(StrEnum):
    """Where an exception is in its lifecycle."""

    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"


class Resolution(StrEnum):
    """How an exception was resolved."""

    AUTO_RULE = "AUTO_RULE"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"
    SOURCE_CORRECTED = "SOURCE_CORRECTED"


class IllegalTransitionError(NorthstarError, ValueError):
    """Raised for a transition the lifecycle does not allow (FR-M2-05)."""


@dataclass(frozen=True, slots=True)
class Rule:
    """A named, versioned rule that may change an exception's status."""

    rule_id: str
    version: int

    @property
    def actor(self) -> str:
        """The rule as a transition actor: ``rule:<id>@<version>``."""
        return f"rule:{self.rule_id}@{self.version}"


DETECTED: Final = Rule("detected", 1)
"""Opens a newly found exception."""
SOURCE_CORRECTED: Final = Rule("source_corrected", 1)
"""Resolves an exception that a later run no longer finds (the rematch succeeded)."""
LATE_SETTLEMENT_COMPLETED: Final = Rule("late_settlement_completed", 1)
"""Resolves a late settlement once its cash has moved (the bank line matched)."""
SLA_BREACH: Final = Rule("sla_breach", 1)
"""Escalates an exception open longer than its severity's SLA."""

_ALLOWED: Final[dict[Status | None, frozenset[Status]]] = {
    None: frozenset({Status.OPEN}),
    Status.OPEN: frozenset({Status.INVESTIGATING, Status.RESOLVED, Status.ESCALATED}),
    Status.INVESTIGATING: frozenset({Status.RESOLVED, Status.ESCALATED}),
    Status.ESCALATED: frozenset({Status.RESOLVED}),
    Status.RESOLVED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Transition:
    """A requested status change."""

    from_status: Status | None
    to_status: Status
    actor: str
    reason: str
    resolution: Resolution | None = None


def check(t: Transition) -> Transition:
    """Return ``t`` if the lifecycle allows it.

    Raises:
        IllegalTransitionError: The move is not allowed, or a resolution lacks
            its type, actor, or reason.
    """
    if t.to_status not in _ALLOWED[t.from_status]:
        msg = f"{t.from_status} -> {t.to_status} is not an allowed transition"
        raise IllegalTransitionError(msg)
    by_rule, by_user = t.actor.startswith("rule:"), t.actor.startswith("user:")
    if not (by_rule or by_user) or t.actor in {"rule:", "user:"}:
        msg = f"actor {t.actor!r} must be rule:<id>@<version> or user:<name>"
        raise IllegalTransitionError(msg)
    if not t.reason.strip():
        msg = "every transition needs a reason"
        raise IllegalTransitionError(msg)
    if t.to_status is not Status.RESOLVED:
        if t.resolution is not None:
            msg = f"a resolution type only belongs on RESOLVED, not {t.to_status}"
            raise IllegalTransitionError(msg)
        return t
    if t.resolution is None:
        msg = "RESOLVED needs a resolution type"
        raise IllegalTransitionError(msg)
    if t.resolution is Resolution.MANUAL_OVERRIDE and not by_user:
        msg = "a manual override needs a person (user:<name>) as actor"
        raise IllegalTransitionError(msg)
    if t.resolution in {Resolution.AUTO_RULE, Resolution.SOURCE_CORRECTED} and not by_rule:
        msg = f"{t.resolution} needs a rule (rule:<id>@<version>) as actor"
        raise IllegalTransitionError(msg)
    if t.from_status is Status.OPEN and not by_rule:
        msg = "only a rule may resolve an OPEN exception; a person investigates first"
        raise IllegalTransitionError(msg)
    return t


def business_days_open(calendar: MarketCalendar, detected_on: date, as_of: date) -> int:
    """Ledger business days from ``detected_on`` (exclusive) to ``as_of`` (inclusive)."""
    if as_of <= detected_on:
        return 0
    count, day = 0, detected_on
    while day < as_of:
        day = calendar.add_settlement_days(day, 1)
        if day <= as_of:
            count += 1
    return count


def breaches_sla(
    policy: ReconciliationPolicy,
    calendar: MarketCalendar,
    severity: Severity,
    detected_on: date,
    as_of: date,
) -> bool:
    """Whether an exception open since ``detected_on`` is past its SLA on ``as_of`` (KPI-04)."""
    return business_days_open(calendar, detected_on, as_of) > policy.sla_business_days[severity]


def late_settlement_completed(
    exception_type: ExceptionType, reference: str, timing_matched: frozenset[str]
) -> bool:
    """Whether a late settlement's cash has moved.

    ``timing_matched`` holds the references whose bank line this run matched
    under the cash rule ``late_settlement_timing``: the money arrived, late.
    """
    return exception_type is ExceptionType.SETTLEMENT_LATE and reference in timing_matched
