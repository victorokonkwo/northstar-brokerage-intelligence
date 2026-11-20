"""Use cases the orchestrator calls around the pipeline: calendar checks, lineage, alerts.

They write only to the object store's ``audit/`` area, with put-if-absent,
so an archived lineage file or a recorded alert is never overwritten.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

from northstar.domain.calendar import MarketCalendar
from northstar.ingestion.pipeline import ObjectStore

LINEAGE_PREFIX = "audit/lineage"
ALERT_PREFIX = "audit/alerts"


def is_ledger_business_day(calendar: MarketCalendar, day: date) -> bool:
    """Whether ``day`` is a ledger business day, i.e. a date the pipeline publishes."""
    return calendar.is_settlement_day(day)


def archive_lineage(store: ObjectStore, events_file: Path, run_id: str) -> str:
    """Copy an OpenLineage JSON Lines file to ``audit/lineage/<run_id>.jsonl``; return the key.

    Raises:
        ValueError: The file is empty or a line is not a JSON object with an
            ``eventType`` (nothing is archived).
        FileExistsError: Lineage for ``run_id`` was already archived.
    """
    body = events_file.read_bytes()
    lines = [line for line in body.decode("utf-8").split("\n") if line.strip()]
    if not lines:
        msg = f"{events_file} has no lineage events"
        raise ValueError(msg)
    for n, line in enumerate(lines, start=1):
        event = json.loads(line)
        if not isinstance(event, dict) or "eventType" not in event:
            msg = f"{events_file} line {n} is not an OpenLineage run event"
            raise ValueError(msg)
    key = f"{LINEAGE_PREFIX}/{run_id}.jsonl"
    if not store.put_if_absent(key, body):
        msg = f"lineage for run {run_id} is already archived at {key}"
        raise FileExistsError(msg)
    return key


@dataclass(frozen=True, slots=True)
class Alert:
    """A pipeline task that failed after its retries."""

    dag_id: str
    task_id: str
    run_id: str
    try_number: int
    message: str
    raised_at: datetime


def record_alert(store: ObjectStore, alert: Alert) -> str:
    """Log the alert at ERROR and store it under ``audit/alerts/``; return the key.

    Raises:
        FileExistsError: This try of this task already has an alert.
    """
    key = f"{ALERT_PREFIX}/{alert.dag_id}/{alert.run_id}/{alert.task_id}-try{alert.try_number}.json"
    record = asdict(alert) | {"raised_at": alert.raised_at.isoformat()}
    logging.getLogger(__name__).error(
        "pipeline task failed: %s.%s run %s try %d: %s",
        alert.dag_id,
        alert.task_id,
        alert.run_id,
        alert.try_number,
        alert.message,
        extra={"job": "alert", "run_id": alert.run_id},
    )
    if not store.put_if_absent(key, json.dumps(record, sort_keys=True, indent=2).encode()):
        msg = f"an alert already exists at {key}"
        raise FileExistsError(msg)
    return key
