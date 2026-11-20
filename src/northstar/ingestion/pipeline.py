"""One ingestion run: validate, deduplicate, partition, write, and record a manifest.

Guarantees (FR-M1-01 to FR-M1-03):

- **Append-only.** Every object is written with put-if-absent, so nothing in
  the raw layer is ever overwritten. Raw and quarantine objects are
  content-addressed (``part-<sha256>.jsonl``), so replaying the same input
  writes no new data objects, only a manifest for the attempt.
- **Raw is as received.** Accepted lines are stored byte for byte, in input
  order, partitioned by domain, entity, and event date.
- **Nothing is dropped silently.** Each line is accepted, an exact duplicate
  of an earlier line (counted), or quarantined with a reason. Lines that
  share an ID but differ are all quarantined: the run does not guess which
  one is right.
- **A bad load fails loudly.** If the quarantine rate exceeds the policy's
  threshold, only the quarantine objects and a ``FAILED`` manifest are
  written, and the run raises ``QuarantineThresholdError``.

Deduplication across loads happens in the warehouse load, which inserts only
new IDs and refuses an ID whose stored content differs.
"""

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from northstar.common.exceptions import NorthstarError
from northstar.domain.calendar import MarketCalendar
from northstar.ingestion.sources import (
    INGESTED_RECORD_MODELS,
    Accepted,
    Rejected,
    RejectionKind,
    classify_event,
    classify_record,
)

MANIFEST_PREFIX = "audit/ingestion_manifests"


class ObjectStore(Protocol):
    """The object store as ingestion needs it."""

    def put_if_absent(self, key: str, body: bytes) -> bool:
        """Write ``body`` at ``key`` unless it exists; return whether it was written."""
        ...

    def get(self, key: str) -> bytes:
        """Read the object at ``key``."""
        ...


class QuarantineThresholdError(NorthstarError):
    """Raised when a load quarantines more than its policy allows (FR-M1-02)."""


class QuarantinePolicy(BaseModel):
    """Versioned ingestion policy (illustrative)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    illustrative: bool
    max_quarantine_rate: Annotated[Decimal, Field(ge=0, le=1)]


class RunStatus(StrEnum):
    """Outcome of an ingestion run."""

    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FileEntry(_Frozen):
    """One input file and what became of its lines."""

    path: str
    sha256: str
    lines: int
    accepted: int
    duplicates: int
    quarantined: int


class ObjectEntry(_Frozen):
    """One object the run produced, whether newly written or already present."""

    key: str
    area: Literal["raw", "quarantine"]
    entity: str
    event_date: date | None
    rows: int
    sha256: str
    written: bool


class IngestionManifest(_Frozen):
    """The audit record of one run (``audit/ingestion_manifests/<run_id>.json``)."""

    run_id: str
    status: RunStatus
    ingested_at: datetime
    code_version: str
    quarantine_policy_version: str
    files: tuple[FileEntry, ...]
    objects: tuple[ObjectEntry, ...]
    schema_versions: dict[str, list[int]]
    quarantine_reasons: dict[str, int]
    quarantine_rate: Decimal

    def raw_objects(self) -> tuple[ObjectEntry, ...]:
        """The raw-layer objects holding this run's accepted lines."""
        return tuple(o for o in self.objects if o.area == "raw")


@dataclass(frozen=True, slots=True)
class SourceFile:
    """An input file: canonical events, or one external record set."""

    path: Path
    record_set: str | None  # None for canonical events

    @classmethod
    def for_dataset(cls, root: Path) -> tuple["SourceFile", ...]:
        """The simulator's output files under ``root``, canonical first.

        Raises:
            FileNotFoundError: The canonical events file is missing.
        """
        events = root / "canonical" / "events.jsonl"
        if not events.is_file():
            msg = f"no canonical events file at {events}"
            raise FileNotFoundError(msg)
        external = [
            cls(root / "external" / f"{name}.jsonl", name)
            for name in sorted(INGESTED_RECORD_MODELS)
            if (root / "external" / f"{name}.jsonl").is_file()
        ]
        return (cls(events, None), *external)


def ingest(
    store: ObjectStore,
    files: Sequence[SourceFile],
    run_id: str,
    policy: QuarantinePolicy,
    calendars: Mapping[str, MarketCalendar],
    code_version: str,
    now: datetime,
) -> IngestionManifest:
    """Ingest ``files`` into the raw layer and write the run's manifest.

    Raises:
        QuarantineThresholdError: The quarantine rate exceeds the policy; no
            raw objects were written, and the manifest says ``FAILED``.
        FileExistsError: A manifest for ``run_id`` already exists.
    """
    file_entries: list[FileEntry] = []
    accepted: list[Accepted] = []
    rejected: list[tuple[str, int, Rejected]] = []
    for source in files:
        entry, kept, refused = _read(source, calendars)
        file_entries.append(entry)
        accepted.extend(kept)
        rejected.extend(refused)

    total = sum(f.lines for f in file_entries)
    rate = Decimal(len(rejected)) / total if total else Decimal(0)
    status = RunStatus.FAILED if rate > policy.max_quarantine_rate else RunStatus.SUCCEEDED

    objects = [_write_quarantine(store, kind, rows) for kind, rows in _by_kind(rejected).items()]
    if status is RunStatus.SUCCEEDED:
        objects.extend(_write_raw(store, accepted))

    versions: defaultdict[str, set[int]] = defaultdict(set)
    for a in accepted:
        versions[a.entity].add(a.schema_version)
    manifest = IngestionManifest(
        run_id=run_id,
        status=status,
        ingested_at=now.astimezone(UTC),
        code_version=code_version,
        quarantine_policy_version=policy.version,
        files=tuple(file_entries),
        objects=tuple(objects),
        schema_versions={k: sorted(v) for k, v in sorted(versions.items())},
        quarantine_reasons=dict(sorted(Counter(r.reason for _, _, r in rejected).items())),
        quarantine_rate=rate.quantize(Decimal("0.000001")),
    )
    key = f"{MANIFEST_PREFIX}/{run_id}.json"
    if not store.put_if_absent(key, manifest.model_dump_json(indent=2).encode()):
        msg = f"a manifest for run {run_id} already exists"
        raise FileExistsError(msg)
    if status is RunStatus.FAILED:
        msg = (
            f"run {run_id} quarantined {len(rejected)} of {total} lines ({rate:.4%}), over the "
            f"{policy.max_quarantine_rate:.4%} threshold of policy {policy.version}"
        )
        raise QuarantineThresholdError(msg)
    return manifest


def read_manifest(store: ObjectStore, run_id: str) -> IngestionManifest:
    """Read a run's manifest back from the store."""
    return IngestionManifest.model_validate_json(store.get(f"{MANIFEST_PREFIX}/{run_id}.json"))


def _read(
    source: SourceFile, calendars: Mapping[str, MarketCalendar]
) -> tuple[FileEntry, list[Accepted], list[tuple[str, int, Rejected]]]:
    data = source.path.read_bytes()
    lines = [line for line in data.decode("utf-8").split("\n") if line.strip()]
    results = [
        (
            n,
            classify_event(line, calendars)
            if source.record_set is None
            else classify_record(source.record_set, line),
        )
        for n, line in enumerate(lines, start=1)
    ]
    first: dict[str, Accepted] = {}
    conflicting: set[str] = set()
    for _, result in results:
        if isinstance(result, Accepted):
            seen = first.setdefault(result.record_id, result)
            if seen.line != result.line:
                conflicting.add(result.record_id)

    kept: list[Accepted] = []
    refused: list[tuple[str, int, Rejected]] = []
    duplicates = 0
    emitted: set[str] = set()
    for n, result in results:
        if isinstance(result, Rejected):
            refused.append((str(source.path), n, result))
        elif result.record_id in conflicting:
            reason = f"{result.record_id} appears with different content in this input"
            refused.append(
                (
                    str(source.path),
                    n,
                    Rejected(RejectionKind.BUSINESS_RULE_FAILURE, reason, result.line),
                )
            )
        elif result.record_id in emitted:
            duplicates += 1
        else:
            emitted.add(result.record_id)
            kept.append(result)
    entry = FileEntry(
        path=str(source.path),
        sha256=hashlib.sha256(data).hexdigest(),
        lines=len(lines),
        accepted=len(kept),
        duplicates=duplicates,
        quarantined=len(refused),
    )
    return entry, kept, refused


def _by_kind(
    rejected: list[tuple[str, int, Rejected]],
) -> dict[RejectionKind, list[tuple[str, int, Rejected]]]:
    grouped: dict[RejectionKind, list[tuple[str, int, Rejected]]] = {}
    for row in rejected:
        grouped.setdefault(row[2].kind, []).append(row)
    return dict(sorted(grouped.items()))


def _write_quarantine(
    store: ObjectStore, kind: RejectionKind, rows: list[tuple[str, int, Rejected]]
) -> ObjectEntry:
    body = "".join(
        json.dumps(
            {"source_file": path, "line_number": n, "reason": r.reason, "line": r.line},
            sort_keys=True,
        )
        + "\n"
        for path, n, r in rows
    ).encode()
    digest = hashlib.sha256(body).hexdigest()
    key = f"quarantine/{kind}/part-{digest}.jsonl"
    written = store.put_if_absent(key, body)
    return ObjectEntry(
        key=key,
        area="quarantine",
        entity=str(kind),
        event_date=None,
        rows=len(rows),
        sha256=digest,
        written=written,
    )


def _write_raw(store: ObjectStore, accepted: list[Accepted]) -> list[ObjectEntry]:
    partitions: dict[tuple[str, str, date], list[str]] = {}
    for a in accepted:
        partitions.setdefault((a.domain, a.entity, a.event_date), []).append(a.line)
    objects = []
    for (domain, entity, day), lines in sorted(partitions.items()):
        body = "".join(line + "\n" for line in lines).encode()
        digest = hashlib.sha256(body).hexdigest()
        key = f"raw/{domain}/{entity}/event_date={day.isoformat()}/part-{digest}.jsonl"
        written = store.put_if_absent(key, body)
        objects.append(
            ObjectEntry(
                key=key,
                area="raw",
                entity=entity,
                event_date=day,
                rows=len(lines),
                sha256=digest,
                written=written,
            )
        )
    return objects
