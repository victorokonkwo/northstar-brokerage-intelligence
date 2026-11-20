"""The reconciliation policy: configured values, validation, and severity."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from northstar.domain.reconciliation import ExceptionType, ReconciliationPolicy, Severity
from northstar.infrastructure.config_loader import load_policy

from factories import CONFIG


@pytest.fixture(scope="module")
def policy() -> ReconciliationPolicy:
    return load_policy(CONFIG / "reconciliation" / "policy.yaml", ReconciliationPolicy)


def test_the_configured_values_are_the_approved_ones(policy: ReconciliationPolicy) -> None:
    assert policy.version == "2026.1"
    assert policy.tolerances.price_per_unit == Decimal("0.0001")
    assert (policy.tolerances.amount, policy.tolerances.quantity) == (Decimal(0), Decimal(0))
    s = policy.severity
    assert (s.critical_cad, s.high_cad, s.medium_cad) == (
        Decimal(10000),
        Decimal(1000),
        Decimal(100),
    )
    assert policy.sla_business_days == {
        Severity.CRITICAL: 1,
        Severity.HIGH: 2,
        Severity.MEDIUM: 5,
        Severity.LOW: 10,
    }


@pytest.mark.parametrize(
    ("impact", "expected"),
    [
        ("10000.00", Severity.CRITICAL),
        ("9999.99", Severity.HIGH),
        ("1000.00", Severity.HIGH),
        ("999.99", Severity.MEDIUM),
        ("100.00", Severity.MEDIUM),
        ("99.99", Severity.LOW),
        ("0.00", Severity.LOW),
        ("-10000.00", Severity.CRITICAL),  # the absolute impact counts
    ],
)
def test_severity_follows_the_cad_thresholds(
    policy: ReconciliationPolicy, impact: str, expected: Severity
) -> None:
    assert policy.severity_of(ExceptionType.FEE_MISMATCH, Decimal(impact)) is expected


def test_some_types_are_always_critical_and_late_settlements_at_least_medium(
    policy: ReconciliationPolicy,
) -> None:
    for t in (
        ExceptionType.CASH_BREAK,
        ExceptionType.MISSING_INTERNAL,
        ExceptionType.POSITION_MISMATCH,
    ):
        assert policy.severity_of(t, Decimal("0.01")) is Severity.CRITICAL
    assert policy.severity_of(ExceptionType.SETTLEMENT_LATE, Decimal("1.00")) is Severity.MEDIUM
    assert policy.severity_of(ExceptionType.SETTLEMENT_LATE, Decimal("5000")) is Severity.HIGH


def test_only_critical_and_high_block_publication(policy: ReconciliationPolicy) -> None:
    assert [s for s in Severity if policy.blocks_publication(s)] == [
        Severity.CRITICAL,
        Severity.HIGH,
    ]


def test_inverted_thresholds_and_a_missing_sla_are_refused(policy: ReconciliationPolicy) -> None:
    raw = policy.model_dump(mode="json")
    raw["severity"]["high_cad"] = "20000"
    with pytest.raises(ValidationError, match="critical >= high >= medium"):
        ReconciliationPolicy.model_validate(raw)
    raw = policy.model_dump(mode="json")
    del raw["sla_business_days"]["LOW"]
    with pytest.raises(ValidationError, match="no SLA for severities"):
        ReconciliationPolicy.model_validate(raw)
