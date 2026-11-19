"""Use case: publish a business date's marts as a versioned snapshot (ADR 006, FR-M1-06).

For each business date, ``publish_date`` hashes what dbt built for that date
(the four gate marts, without lineage-only columns) and compares it with the
date's latest version:

- **Unchanged** content creates nothing (INV-09: a re-run with identical
  inputs and code gives identical results, so there is nothing to publish).
- **Changed** content, or a first run, creates version n+1. Version 1 has no
  restatement reason; later versions always do, naming the marts that changed.
- A new version is ``DRAFT``, and becomes ``PUBLISHED`` only when its gate
  passes: KPI-01 = 0 for the date. The previously published version becomes
  ``SUPERSEDED`` at the same instant, and stays queryable. Phase 4 adds the
  second condition of domain model §7.4 (no blocking reconciliation exception).

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
from northstar.infrastructure.snapshots import (
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


def publish_date(
    conn: psycopg.Connection[Any],
    source: str,
    published: str,
    business_date: date,
    run_id: str,
    code_version: str,
    now: datetime,
    note: str | None = None,
) -> DatePublication:
    """Publish one business date from the dbt schema ``source`` into ``published``.

    Raises:
        ValueError: ``business_date`` is not a reporting date in ``source``.
        PublicationBlockedError: KPI-01 is not 0; the version is recorded as
            DRAFT (committed) and not published.
    """
    if business_date not in reporting_dates(conn, source):
        msg = f"{business_date} is not a reporting date in {source}"
        raise ValueError(msg)
    with conn.transaction():
        ensure_published(conn, published, source)
        rows = {
            mart: read_rows(conn, source, mart, business_date, content_columns(conn, source, mart))
            for mart in PUBLISHED_MARTS
        }
        kpi_01 = unbalanced_batches(conn, source, business_date)
        overall, per_mart = content_hash(rows, kpi_01)
        versions = history(conn, published, business_date)
        latest = versions[-1] if versions else None
        if latest is not None and latest.content_sha256 == overall:
            return DatePublication(
                business_date,
                Outcome.UNCHANGED,
                latest.snapshot_version,
                overall,
                latest.restatement_reason,
                None,
            )
        number = 1 if latest is None else latest.snapshot_version + 1
        reason = None if latest is None else _reason(latest, per_mart, note)
        record = VersionRecord(
            business_date, number, overall, per_mart, "DRAFT", None, None, reason
        )
        record_version(conn, published, record, kpi_01, run_id, code_version, source, now)
        log_status(conn, published, business_date, number, "DRAFT", now, reason or "first version")
        current = next((v for v in versions if v.status == "PUBLISHED"), None)
        if kpi_01 == 0:
            log_status(conn, published, business_date, number, "PUBLISHED", now, "KPI-01 = 0")
            if current is not None:
                log_status(
                    conn,
                    published,
                    business_date,
                    current.snapshot_version,
                    "SUPERSEDED",
                    now,
                    f"superseded by v{number}",
                )
    if kpi_01 != 0:
        msg = (
            f"{business_date} v{number} stays DRAFT: KPI-01 is {kpi_01} "
            "(unbalanced journal batches must be 0)"
        )
        raise PublicationBlockedError(msg)
    return DatePublication(
        business_date,
        Outcome.PUBLISHED,
        number,
        overall,
        reason,
        current.snapshot_version if current is not None else None,
    )


def publish_all(
    conn: psycopg.Connection[Any],
    source: str,
    published: str,
    run_id: str,
    code_version: str,
    now: datetime,
    note: str | None = None,
) -> list[DatePublication]:
    """Publish every reporting date in ``source``, oldest first.

    Raises:
        PublicationBlockedError: A date fails its gate. Earlier dates stay
            published; later dates are not attempted.
    """
    return [
        publish_date(conn, source, published, day, run_id, code_version, now, note)
        for day in reporting_dates(conn, source)
    ]


def _reason(latest: VersionRecord, per_mart: dict[str, str], note: str | None) -> str:
    changed = sorted(m for m, h in per_mart.items() if latest.mart_sha256.get(m) != h)
    what = ", ".join(changed) if changed else "KPI-01"
    verb = "restates" if latest.status != "DRAFT" else "replaces draft"
    text = f"{verb} v{latest.snapshot_version}: {what} changed"
    return f"{text}; {note}" if note else text
