"""The exception lifecycle: allowed moves, proof of resolution, and SLA ageing."""

from datetime import date
from itertools import product

import pytest

from northstar.domain.lifecycle import (
    LATE_SETTLEMENT_COMPLETED,
    IllegalTransitionError,
    Resolution,
    Status,
    Transition,
    breaches_sla,
    business_days_open,
    check,
    late_settlement_completed,
)
from northstar.domain.reconciliation import ExceptionType, ReconciliationPolicy, Severity
from northstar.infrastructure.config_loader import load_policy

from factories import CONFIG, xtse

RULE = LATE_SETTLEMENT_COMPLETED.actor
USER = "user:test"

# Domain model §7.4, as data: the only moves that exist.
ALLOWED = {
    (None, Status.OPEN),
    (Status.OPEN, Status.INVESTIGATING),
    (Status.OPEN, Status.RESOLVED),
    (Status.OPEN, Status.ESCALATED),
    (Status.INVESTIGATING, Status.RESOLVED),
    (Status.INVESTIGATING, Status.ESCALATED),
    (Status.ESCALATED, Status.RESOLVED),
}


def _legal(frm: Status | None, to: Status) -> Transition:
    if to is Status.RESOLVED:
        return Transition(frm, to, RULE, "rule fired", Resolution.AUTO_RULE)
    return Transition(frm, to, RULE, "rule fired")


@pytest.mark.parametrize(("frm", "to"), list(product([None, *Status], list(Status))))
def test_only_the_lifecycle_moves_are_allowed(frm: Status | None, to: Status) -> None:
    if (frm, to) in ALLOWED:
        assert check(_legal(frm, to)) == _legal(frm, to)
    else:
        with pytest.raises(IllegalTransitionError, match="not an allowed transition"):
            check(_legal(frm, to))


class TestResolutionProof:
    def test_a_manual_override_needs_a_person_and_a_reason(self) -> None:
        ok = Transition(
            Status.INVESTIGATING,
            Status.RESOLVED,
            USER,
            "bank confirmed",
            Resolution.MANUAL_OVERRIDE,
        )
        assert check(ok) == ok
        with pytest.raises(IllegalTransitionError, match="needs a person"):
            check(
                Transition(
                    Status.INVESTIGATING, Status.RESOLVED, RULE, "x", Resolution.MANUAL_OVERRIDE
                )
            )
        with pytest.raises(IllegalTransitionError, match="needs a reason"):
            check(
                Transition(
                    Status.INVESTIGATING, Status.RESOLVED, USER, "  ", Resolution.MANUAL_OVERRIDE
                )
            )

    def test_rule_resolutions_need_a_rule(self) -> None:
        for resolution in (Resolution.AUTO_RULE, Resolution.SOURCE_CORRECTED):
            with pytest.raises(IllegalTransitionError, match="needs a rule"):
                check(Transition(Status.ESCALATED, Status.RESOLVED, USER, "x", resolution))

    def test_resolving_needs_a_type_and_only_resolving_has_one(self) -> None:
        with pytest.raises(IllegalTransitionError, match="needs a resolution type"):
            check(Transition(Status.ESCALATED, Status.RESOLVED, USER, "x"))
        with pytest.raises(IllegalTransitionError, match="only belongs on RESOLVED"):
            check(
                Transition(Status.OPEN, Status.INVESTIGATING, USER, "x", Resolution.MANUAL_OVERRIDE)
            )

    def test_a_person_must_investigate_before_resolving(self) -> None:
        with pytest.raises(IllegalTransitionError, match="only a rule may resolve an OPEN"):
            check(Transition(Status.OPEN, Status.RESOLVED, USER, "x", Resolution.MANUAL_OVERRIDE))

    def test_an_actor_must_say_what_it_is(self) -> None:
        for actor in ("victor", "rule:", "user:", ""):
            with pytest.raises(IllegalTransitionError, match="actor"):
                check(Transition(Status.OPEN, Status.INVESTIGATING, actor, "x"))


class TestAgeing:
    def test_age_counts_ledger_business_days_across_weekends_and_holidays(self) -> None:
        cal = xtse()
        # Fri 2026-02-13 -> Tue 2026-02-17: the weekend and Family Day (02-16) don't count.
        assert business_days_open(cal, date(2026, 2, 13), date(2026, 2, 17)) == 1
        assert business_days_open(cal, date(2026, 2, 13), date(2026, 2, 13)) == 0
        assert business_days_open(cal, date(2026, 3, 2), date(2026, 3, 9)) == 5

    def test_sla_is_breached_only_after_its_business_days(self) -> None:
        policy = load_policy(CONFIG / "reconciliation" / "policy.yaml", ReconciliationPolicy)
        cal = xtse()
        assert not breaches_sla(
            policy, cal, Severity.MEDIUM, date(2026, 3, 2), date(2026, 3, 9)
        )  # 5 days
        assert breaches_sla(
            policy, cal, Severity.MEDIUM, date(2026, 3, 2), date(2026, 3, 10)
        )  # 6 days
        assert breaches_sla(policy, cal, Severity.CRITICAL, date(2026, 3, 2), date(2026, 3, 4))
        assert not breaches_sla(policy, cal, Severity.CRITICAL, date(2026, 3, 2), date(2026, 3, 3))


def test_a_late_settlement_completes_only_when_its_cash_was_matched() -> None:
    timing = frozenset({"stl_1"})
    assert late_settlement_completed(ExceptionType.SETTLEMENT_LATE, "stl_1", timing)
    assert not late_settlement_completed(ExceptionType.SETTLEMENT_LATE, "stl_2", timing)
    assert not late_settlement_completed(ExceptionType.CASH_BREAK, "stl_1", timing)
