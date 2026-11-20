"""Use cases: reconcile the warehouse, and verify the result against a defect manifest.

``reconcile_warehouse`` reads both sides of the seven reconciliations from the
dbt models as of a date, matches them in stages, classifies the breaks under
the versioned policy, and stores matches and exceptions append-only in one
transaction (FR-M2-02 to FR-M2-04, FR-M2-07).

``verify_against_manifest`` is NFR-03's acceptance check on the real pipeline:
the stored exceptions must equal the simulator's defect manifest exactly, by
expected exception type, reference, and impact, with nothing missing and
nothing extra.
"""

import json
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from opentelemetry import trace

from northstar.domain.matching import reconcile_all
from northstar.domain.reconciliation import ReconciliationPolicy, classify
from northstar.infrastructure.config_loader import load_policy
from northstar.infrastructure.recon_store import (
    RunRecord,
    ensure_recon_tables,
    last_reporting_date,
    read_inputs,
    store_run,
    stored_exceptions,
)


@dataclass(frozen=True, slots=True)
class ReconcileOutcome:
    """What a run found and how much of it was new."""

    run_id: str
    as_of: date
    matches: int
    exceptions: int
    new_matches: int
    new_exceptions: int
    by_type: dict[str, int]


def reconcile_warehouse(
    conn: psycopg.Connection[Any],
    config_dir: Path,
    source: str,
    recon_schema: str,
    run_id: str,
    code_version: str,
    as_of: date | None = None,
) -> ReconcileOutcome:
    """Reconcile everything known by ``as_of`` (default: the last reporting date).

    Raises:
        LoadConflictError: A stored match or exception differs from this run's.
        PolicyNotFoundError: A USD exception's date has no applicable rate.
    """
    with trace.get_tracer(__name__).start_as_current_span("northstar.reconcile") as span:
        policy = load_policy(config_dir / "reconciliation" / "policy.yaml", ReconciliationPolicy)
        day = as_of or last_reporting_date(conn, source)
        inputs, rates = read_inputs(conn, source, day)
        result = reconcile_all(inputs, policy.tolerances)
        exceptions = classify(result.breaks, policy, rates)
        run = RunRecord(run_id, day, source, policy.version, code_version, datetime.now(UTC))
        with conn.transaction():
            ensure_recon_tables(conn, recon_schema)
            new_matches, new_exceptions = store_run(
                conn, recon_schema, run, result.matches, exceptions
            )
        by_type = dict(sorted(Counter(str(x.exception_type) for x in exceptions).items()))
        span.set_attributes({"northstar.run_id": run_id, "northstar.exceptions": len(exceptions)})
        logging.getLogger(__name__).info(
            "reconciled as of %s: %d matches, %d exceptions",
            day,
            len(result.matches),
            len(exceptions),
            extra={"job": "reconcile", "run_id": run_id},
        )
        return ReconcileOutcome(
            run_id,
            day,
            len(result.matches),
            len(exceptions),
            new_matches,
            new_exceptions,
            by_type,
        )


@dataclass(frozen=True, slots=True)
class Verification:
    """The difference between the stored exceptions and the manifest."""

    expected: int
    found: int
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]
    impact_differences: tuple[str, ...]

    @property
    def exact(self) -> bool:
        """Whether every defect was found, classified, and valued, and nothing else."""
        return not (self.missing or self.unexpected or self.impact_differences)


def verify_against_manifest(
    conn: psycopg.Connection[Any], recon_schema: str, manifest_path: Path
) -> Verification:
    """Compare stored exceptions with a defect manifest (JSON Lines)."""
    manifest = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    expected = {(m["expected_exception"], m["broker_reference"]): m for m in manifest}
    found = {
        (x["exception_type"], x["reference"]): x for x in stored_exceptions(conn, recon_schema)
    }
    impact_differences = tuple(
        f"{key}: manifest {m['impact_amount']} {m['impact_currency']}, "
        f"found {found[key]['impact_amount']} {found[key]['impact_currency']}"
        for key, m in sorted(expected.items())
        if key in found
        and (
            Decimal(m["impact_amount"]) != found[key]["impact_amount"]
            or m["impact_currency"] != found[key]["impact_currency"].strip()
        )
    )
    return Verification(
        expected=len(expected),
        found=len(found),
        missing=tuple(f"{t} {r}" for t, r in sorted(set(expected) - set(found))),
        unexpected=tuple(f"{t} {r}" for t, r in sorted(set(found) - set(expected))),
        impact_differences=impact_differences,
    )
