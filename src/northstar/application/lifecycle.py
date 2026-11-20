"""Use cases: apply the lifecycle rules after a run, and change an exception by hand.

After each reconciliation run, ``apply_rules`` proposes transitions with
versioned rules, checks each against the lifecycle, and appends it:

1. ``detected@1``: every new exception opens.
2. ``source_corrected@1``: an unresolved exception dated on or before the
   run's as-of date that the run no longer finds is resolved. A corrected
   record arrived and the rematch succeeded.
3. ``late_settlement_completed@1``: a late settlement whose bank line was
   matched as a timing difference is resolved.
4. ``sla_breach@1``: an unresolved exception open longer than its severity's
   SLA (ledger business days) is escalated.

A rerun proposes nothing new, so it appends nothing. ``transition_exception``
is the operator's path: investigate, escalate, or resolve with a reason.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import psycopg
from psycopg import sql

from northstar.domain.calendar import MarketCalendar
from northstar.domain.lifecycle import (
    DETECTED,
    LATE_SETTLEMENT_COMPLETED,
    SLA_BREACH,
    SOURCE_CORRECTED,
    Resolution,
    Status,
    Transition,
    breaches_sla,
    check,
    late_settlement_completed,
)
from northstar.domain.reconciliation import ExceptionType, ReconciliationPolicy, Severity
from northstar.infrastructure.recon_store import (
    ExceptionState,
    TransitionRecord,
    append_transitions,
    current_states,
)


@dataclass(frozen=True, slots=True)
class LifecycleOutcome:
    """How many transitions each rule appended in a run."""

    opened: int
    source_corrected: int
    auto_resolved: int
    escalated: int


def apply_rules(
    conn: psycopg.Connection[Any],
    schema: str,
    found: frozenset[str],
    timing_matched: frozenset[str],
    policy: ReconciliationPolicy,
    calendar: MarketCalendar,
    as_of: date,
    run_id: str,
    now: datetime,
) -> LifecycleOutcome:
    """Apply the four rules to the stored exceptions; ``found`` is this run's exception IDs."""
    states = current_states(conn, schema)
    out: list[TransitionRecord] = []
    counts = {"opened": 0, "source_corrected": 0, "auto_resolved": 0, "escalated": 0}

    def propose(state: ExceptionState, t: Transition, key: str) -> ExceptionState:
        check(t)
        out.append(
            TransitionRecord(
                state.exception_id,
                state.sequence + 1,
                t.from_status,
                t.to_status,
                t.actor,
                t.reason,
                t.resolution,
                now,
                run_id,
            )
        )
        counts[key] += 1
        return ExceptionState(
            state.exception_id,
            state.exception_type,
            state.severity,
            state.reference,
            state.business_date,
            t.to_status,
            state.sequence + 1,
        )

    for state in sorted(states.values(), key=lambda s: s.exception_id):
        current = state
        if current.status is None:
            current = propose(
                current,
                Transition(None, Status.OPEN, DETECTED.actor, "found by reconciliation"),
                "opened",
            )
        if current.status == Status.RESOLVED:
            continue
        if current.exception_id not in found and current.business_date <= as_of:
            propose(
                current,
                Transition(
                    _status(current),
                    Status.RESOLVED,
                    SOURCE_CORRECTED.actor,
                    f"no longer found by run {run_id}",
                    Resolution.SOURCE_CORRECTED,
                ),
                "source_corrected",
            )
            continue
        if late_settlement_completed(
            ExceptionType(current.exception_type), current.reference, timing_matched
        ):
            propose(
                current,
                Transition(
                    _status(current),
                    Status.RESOLVED,
                    LATE_SETTLEMENT_COMPLETED.actor,
                    "the late settlement's cash has moved",
                    Resolution.AUTO_RULE,
                ),
                "auto_resolved",
            )
            continue
        if current.status in (Status.OPEN, Status.INVESTIGATING) and breaches_sla(
            policy, calendar, Severity(current.severity), current.business_date, as_of
        ):
            propose(
                current,
                Transition(
                    _status(current),
                    Status.ESCALATED,
                    SLA_BREACH.actor,
                    f"open past the {current.severity} SLA "
                    f"({policy.sla_business_days[Severity(current.severity)]} "
                    f"business days) on {as_of}",
                ),
                "escalated",
            )
    append_transitions(conn, schema, out)
    return LifecycleOutcome(**counts)


def _status(state: ExceptionState) -> Status:
    """The state's status; every stored exception has one after it is opened."""
    if state.status is None:  # pragma: no cover - apply_rules opens first
        msg = f"exception {state.exception_id} has no status"
        raise LookupError(msg)
    return Status(state.status)


def transition_exception(
    conn: psycopg.Connection[Any],
    schema: str,
    exception_id: str,
    to_status: Status,
    actor: str,
    reason: str,
    resolution: Resolution | None,
    now: datetime,
) -> TransitionRecord:
    """Move one exception by hand, if the lifecycle allows it.

    Raises:
        LookupError: No exception has this ID.
        IllegalTransitionError: The move, actor, reason, or resolution is not allowed.
    """
    with conn.transaction():
        with conn.cursor() as cur:  # serialise changes to this exception
            cur.execute(
                sql.SQL("select 1 from {} where exception_id = %s for update").format(
                    sql.Identifier(schema, "exceptions")
                ),
                (exception_id,),
            )
            if cur.fetchone() is None:
                msg = f"no exception {exception_id}"
                raise LookupError(msg)
        state = current_states(conn, schema)[exception_id]
        current = Status(state.status) if state.status is not None else None
        t = check(Transition(current, to_status, actor, reason, resolution))
        record = TransitionRecord(
            exception_id,
            state.sequence + 1,
            t.from_status,
            t.to_status,
            t.actor,
            t.reason,
            t.resolution,
            now,
            None,
        )
        append_transitions(conn, schema, [record])
    return record
