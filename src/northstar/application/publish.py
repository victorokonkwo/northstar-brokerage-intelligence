"""Use case: publish a business date's marts as a versioned snapshot (ADR 006, FR-M1-06).

For each business date, ``publish_date`` hashes what dbt built for that date
(the four gate marts, without lineage-only columns) and compares it with the
date's latest version:

- **Unchanged** content creates nothing (INV-09: a re-run with identical
  inputs and code gives identical results, so there is nothing to publish).
- **Changed** content, or a first run, creates version n+1. Version 1 has no
  restatement reason; later versions always do, naming the marts that changed.
- A new version is ``DRAFT``, and becomes ``PUBLISHED`` only when its gate
  passes. The previously published version becomes ``SUPERSEDED`` at the same
  instant, and stays queryable. A DRAFT whose gate later passes (with
  unchanged content) is published as it is, with no new version.

There are two streams, each in its own published schema:

- ``financial``: the four gate marts. Gate: KPI-01 = 0 for the date, and no
  open blocking (CRITICAL or HIGH) reconciliation exception dated that day
  (domain model §7.4, FR-M2-08).
- ``controls``: the reconciliation KPI marts. Gate: KPI-01 = 0 only, so
  operations can always see the exceptions that block the financial snapshot.

Nothing is recomputed here (ADR 007): the published rows are copies of dbt's.
Everything for one date happens in one transaction.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

import psycopg

from northstar.common.exceptions import PublicationBlockedError
from northstar.domain.reconciliation import ReconciliationPolicy, Severity
from northstar.infrastructure.recon_store import current_states
from northstar.infrastructure.snapshots import (
    CONTROLS_MARTS,
    PUBLISHED_MARTS,
    VersionRecord,
    content_columns,
    ensure_published,
    history,
    log_status,
    read_rows,
    record_version,
    reporting_dates,
    unbalanced_batches,
)


class Stream(StrEnum):
    """Which snapshot a publication belongs to."""

    FINANCIAL = "financial"
    CONTROLS = "controls"


STREAM_MARTS: dict[Stream, tuple[str, ...]] = {
    Stream.FINANCIAL: PUBLISHED_MARTS,
    Stream.CONTROLS: CONTROLS_MARTS,
}


@dataclass(frozen=True, slots=True)
class Gate:
    """What the financial gate checks beyond KPI-01: open blocking exceptions."""

    recon_schema: str
    policy: ReconciliationPolicy


class Outcome(StrEnum):
    """What publishing a date did."""

    UNCHANGED = "UNCHANGED"
    PUBLISHED = "PUBLISHED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class DatePublication:
    """The result for one business date."""

    business_date: date
    outcome: Outcome
    snapshot_version: int
    content_sha256: str
    restatement_reason: str | None
    superseded_version: int | None
    blocked_by: tuple[str, ...] = ()


def content_hash(
    rows_by_mart: Mapping[str, Sequence[Sequence[Any]]], kpi_01: int
) -> tuple[str, dict[str, str]]:
    """SHA-256 of a date's content, overall and per mart.

    Values are written with ``str`` (exact for ``Decimal``, ISO for dates), so
    the hash depends only on the data, never on the run that produced it.
    """
    per_mart = {
        mart: hashlib.sha256(
            json.dumps([[str(v) for v in row] for row in rows], separators=(",", ":")).encode()
        ).hexdigest()
        for mart, rows in sorted(rows_by_mart.items())
    }
    overall = hashlib.sha256(
        json.dumps({"kpi_01": kpi_01, "marts": per_mart}, sort_keys=True).encode()
    ).hexdigest()
    return overall, per_mart


def blocking_exceptions(
    conn: psycopg.Connection[Any], gate: Gate, business_date: date
) -> tuple[str, ...]:
    """IDs of unresolved exceptions dated ``business_date`` whose severity blocks publication."""
    return tuple(
        sorted(
            s.exception_id
            for s in current_states(conn, gate.recon_schema).values()
            if s.business_date == business_date
            and s.status != "RESOLVED"
            and gate.policy.blocks_publication(Severity(s.severity))
        )
    )


def publish_date(
    conn: psycopg.Connection[Any],
    source: str,
    published: str,
    business_date: date,
    run_id: str,
    code_version: str,
    now: datetime,
    note: str | None = None,
    stream: Stream = Stream.FINANCIAL,
    gate: Gate | None = None,
) -> DatePublication:
    """Publish one business date of ``stream`` from the dbt schema ``source`` into ``published``.

    ``gate`` is required for the financial stream (it names the reconciliation
    results to check); the controls stream ignores it.

    Raises:
        ValueError: ``business_date`` is not a reporting date in ``source``, or
            the financial stream has no gate.
        PublicationBlockedError: The gate fails; the version is recorded as
            DRAFT (committed) and not published.
    """
    if stream is Stream.FINANCIAL and gate is None:
        msg = "the financial stream needs a gate (reconciliation results)"
        raise ValueError(msg)
    if business_date not in reporting_dates(conn, source):
        msg = f"{business_date} is not a reporting date in {source}"
        raise ValueError(msg)
    marts = STREAM_MARTS[stream]
    with conn.transaction():
        ensure_published(conn, published, source, marts)
        rows = {
            mart: read_rows(conn, source, mart, business_date, content_columns(conn, source, mart))
            for mart in marts
        }
        kpi_01 = unbalanced_batches(conn, source, business_date)
        blockers = (
            blocking_exceptions(conn, gate, business_date)
            if stream is Stream.FINANCIAL and gate is not None
            else ()
        )
        passes = kpi_01 == 0 and not blockers
        overall, per_mart = content_hash(rows, kpi_01)
        versions = history(conn, published, business_date)
        latest = versions[-1] if versions else None
        current = next((v for v in versions if v.status == "PUBLISHED"), None)
        if latest is not None and latest.content_sha256 == overall:
            if latest.status != "DRAFT" or not passes:
                outcome = Outcome.UNCHANGED if latest.status != "DRAFT" else Outcome.BLOCKED
                return DatePublication(
                    business_date,
                    outcome,
                    latest.snapshot_version,
                    overall,
                    latest.restatement_reason,
                    None,
                    blockers,
                )
            number, reason = latest.snapshot_version, latest.restatement_reason
        else:
            number = 1 if latest is None else latest.snapshot_version + 1
            reason = None if latest is None else _reason(latest, per_mart, note)
            record = VersionRecord(
                business_date, number, overall, per_mart, "DRAFT", None, None, reason
            )
            record_version(
                conn, published, record, kpi_01, run_id, code_version, source, now, marts
            )
            log_status(
                conn, published, business_date, number, "DRAFT", now, reason or "first version"
            )
        if passes:
            log_status(
                conn,
                published,
                business_date,
                number,
                "PUBLISHED",
                now,
                "KPI-01 = 0" + ("; no open blocking exceptions" if gate is not None else ""),
            )
            if current is not None and current.snapshot_version != number:
                log_status(
                    conn,
                    published,
                    business_date,
                    current.snapshot_version,
                    "SUPERSEDED",
                    now,
                    f"superseded by v{number}",
                )
    if not passes:
        why = []
        if kpi_01 != 0:
            why.append(f"KPI-01 is {kpi_01} (unbalanced journal batches must be 0)")
        if blockers:
            why.append(f"{len(blockers)} open blocking exception(s): {', '.join(blockers)}")
        msg = f"{business_date} v{number} stays DRAFT: " + "; ".join(why)
        raise PublicationBlockedError(msg)
    return DatePublication(
        business_date,
        Outcome.PUBLISHED,
        number,
        overall,
        reason,
        current.snapshot_version
        if current is not None and current.snapshot_version != number
        else None,
    )


def publish_all(
    conn: psycopg.Connection[Any],
    source: str,
    published: str,
    run_id: str,
    code_version: str,
    now: datetime,
    note: str | None = None,
    stream: Stream = Stream.FINANCIAL,
    gate: Gate | None = None,
) -> list[DatePublication]:
    """Publish every reporting date of ``stream``, oldest first.

    A blocked date stays DRAFT and is reported as ``BLOCKED`` (with what
    blocks it); the other dates are still published.
    """
    results = []
    for day in reporting_dates(conn, source):
        try:
            results.append(
                publish_date(
                    conn, source, published, day, run_id, code_version, now, note, stream, gate
                )
            )
        except PublicationBlockedError:
            latest = history(conn, published, day)[-1]
            blockers = (
                blocking_exceptions(conn, gate, day)
                if stream is Stream.FINANCIAL and gate is not None
                else ()
            )
            results.append(
                DatePublication(
                    day,
                    Outcome.BLOCKED,
                    latest.snapshot_version,
                    latest.content_sha256,
                    latest.restatement_reason,
                    None,
                    blockers,
                )
            )
    return results


def _reason(latest: VersionRecord, per_mart: dict[str, str], note: str | None) -> str:
    changed = sorted(m for m, h in per_mart.items() if latest.mart_sha256.get(m) != h)
    what = ", ".join(changed) if changed else "KPI-01"
    verb = "restates" if latest.status != "DRAFT" else "replaces draft"
    text = f"{verb} v{latest.snapshot_version}: {what} changed"
    return f"{text}; {note}" if note else text
