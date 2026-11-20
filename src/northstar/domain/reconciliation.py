"""Reconciliation vocabulary and policy (domain model §7).

Exception types, severities, and the versioned policy that sets matching
tolerances, severity thresholds, and SLAs. Severity is a pure function of an
exception's type and its absolute impact in CAD.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from northstar.common.exceptions import PolicyNotFoundError
from northstar.common.ids import derived_id
from northstar.common.money import Currency

if TYPE_CHECKING:
    from northstar.domain.matching import Break

CENT = Decimal("0.01")


class ExceptionType(StrEnum):
    """The reconciliation exception types (domain model §7.2)."""

    MISSING_EXTERNAL = "MISSING_EXTERNAL"
    MISSING_INTERNAL = "MISSING_INTERNAL"
    DUPLICATE_EXTERNAL = "DUPLICATE_EXTERNAL"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    FEE_MISMATCH = "FEE_MISMATCH"
    FX_MISMATCH = "FX_MISMATCH"
    SETTLEMENT_LATE = "SETTLEMENT_LATE"
    SETTLEMENT_AMOUNT_MISMATCH = "SETTLEMENT_AMOUNT_MISMATCH"
    POSITION_MISMATCH = "POSITION_MISMATCH"
    CASH_BREAK = "CASH_BREAK"
    STALE_PRICE = "STALE_PRICE"
    """The vendor repeated an earlier close for a day its market traded (INV-08)."""


class Severity(StrEnum):
    """Exception severity, highest first (domain model §7.3)."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


_RANK = {Severity.LOW: 0, Severity.MEDIUM: 1, Severity.HIGH: 2, Severity.CRITICAL: 3}

NonNegative = Annotated[Decimal, Field(ge=0)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Tolerances(_Frozen):
    """How far internal and external values may differ at the tolerance stage."""

    price_per_unit: NonNegative
    amount: NonNegative
    quantity: NonNegative


class SeverityRules(_Frozen):
    """Severity thresholds in CAD and the types with fixed floors."""

    critical_cad: NonNegative
    high_cad: NonNegative
    medium_cad: NonNegative
    always_critical: frozenset[ExceptionType]
    at_least_medium: frozenset[ExceptionType]
    blocking: frozenset[Severity]

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not self.critical_cad >= self.high_cad >= self.medium_cad:
            msg = "severity thresholds must satisfy critical >= high >= medium"
            raise ValueError(msg)
        return self


class ReconciliationPolicy(_Frozen):
    """Versioned reconciliation policy (illustrative values; config/reconciliation)."""

    version: str
    effective_from: date
    illustrative: Literal[True]
    tolerances: Tolerances
    severity: SeverityRules
    sla_business_days: dict[Severity, Annotated[int, Field(ge=1)]]

    @model_validator(mode="after")
    def _every_severity_has_an_sla(self) -> Self:
        if missing := set(Severity) - set(self.sla_business_days):
            msg = f"no SLA for severities {sorted(missing)}"
            raise ValueError(msg)
        return self

    def severity_of(self, exception_type: ExceptionType, impact_cad: Decimal) -> Severity:
        """The severity of an exception with this type and impact (absolute value, CAD)."""
        if exception_type in self.severity.always_critical:
            return Severity.CRITICAL
        rules, impact = self.severity, abs(impact_cad)
        by_impact = (
            Severity.CRITICAL
            if impact >= rules.critical_cad
            else Severity.HIGH
            if impact >= rules.high_cad
            else Severity.MEDIUM
            if impact >= rules.medium_cad
            else Severity.LOW
        )
        if exception_type in rules.at_least_medium and _RANK[by_impact] < _RANK[Severity.MEDIUM]:
            return Severity.MEDIUM
        return by_impact

    def blocks_publication(self, severity: Severity) -> bool:
        """Whether an open exception at ``severity`` blocks the date's publication."""
        return severity in self.severity.blocking


# --- Classification ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AppliedRate:
    """The USD/CAD rate in force on a business date (fallback policy v1)."""

    rate: Decimal
    rate_date: date
    fx_policy_version: str


@dataclass(frozen=True, slots=True)
class ReconException:
    """A classified exception: a break with its severity and CAD impact.

    ``exception_id`` depends only on what the break is (reconciliation, type,
    reference, and external records), so rerunning with the same inputs finds
    the same exceptions, never duplicates (FR-M2-07).
    """

    exception_id: str
    reconciliation: str
    exception_type: ExceptionType
    severity: Severity
    reference: str
    business_date: date
    impact_amount: Decimal
    impact_currency: Currency
    impact_basis: str
    impact_cad: Decimal | None
    fx_rate_date: date | None
    fx_policy_version: str | None
    external_record_ids: tuple[str, ...]
    detail: str
    policy_version: str


def classify(
    breaks: "Iterable[Break]",
    policy: ReconciliationPolicy,
    rates: Mapping[date, AppliedRate],
) -> tuple[ReconException, ...]:
    """Give each break its severity and CAD impact under ``policy``.

    A USD impact is translated at the rate in force on the break's business
    date and rounded half-even once (ADR 004). A quantity break has no CAD
    impact; its severity comes from its type alone.

    Raises:
        PolicyNotFoundError: A USD break's date has no applicable rate.
    """
    out = []
    for b in breaks:
        rate: AppliedRate | None = None
        if b.impact_basis == "quantity":
            impact_cad = None
        elif b.impact_currency is Currency.CAD:
            impact_cad = b.impact_amount
        else:
            rate = rates.get(b.business_date)
            if rate is None:
                msg = f"no USD/CAD rate applies on {b.business_date} (exception {b.reference})"
                raise PolicyNotFoundError(msg)
            impact_cad = (b.impact_amount * rate.rate).quantize(CENT, rounding=ROUND_HALF_EVEN)
        severity = policy.severity_of(b.exception_type, impact_cad or Decimal(0))
        out.append(
            ReconException(
                exception_id=str(
                    derived_id(
                        "recon_exception",
                        b.reconciliation,
                        b.exception_type,
                        b.reference,
                        *b.external_record_ids,
                    )
                ),
                reconciliation=str(b.reconciliation),
                exception_type=b.exception_type,
                severity=severity,
                reference=b.reference,
                business_date=b.business_date,
                impact_amount=b.impact_amount,
                impact_currency=b.impact_currency,
                impact_basis=b.impact_basis,
                impact_cad=impact_cad,
                fx_rate_date=rate.rate_date if rate is not None else None,
                fx_policy_version=rate.fx_policy_version if rate is not None else None,
                external_record_ids=b.external_record_ids,
                detail=b.detail,
                policy_version=policy.version,
            )
        )
    return tuple(out)
