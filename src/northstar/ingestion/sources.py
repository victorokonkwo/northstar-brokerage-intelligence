"""What each source line is, where it lives in the raw layer, and whether it is acceptable.

Every line is validated against its versioned model: canonical events against
``EVENT_MODELS`` by ``event_type``, external records against ``RECORD_MODELS``
by record set. A line that fails is rejected with a reason, never repaired:

- ``schema_errors``: not JSON, an unknown event type, a schema version newer
  than the code supports (consumers never guess at a newer schema, domain
  model §4.2), or a payload that fails validation.
- ``business_rule_failures``: valid in shape but impossible in the business,
  such as an execution on a day its market does not trade (domain model §5).

Raw layout (system overview §6):
``raw/<domain>/<entity>/event_date=YYYY-MM-DD/``. The event date is the
business date in America/Toronto for events, and the record's own business
date for external records.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, TypeAdapter, ValidationError

from northstar.domain.calendar import MarketCalendar, business_date
from northstar.domain.events import EVENT_MODELS, Event, EventType, TradeExecuted
from northstar.simulation.external import EXTERNAL_RECORD_SCHEMA_VERSION, RECORD_MODELS
from northstar.simulation.history import HISTORY_MODELS


class RejectionKind(StrEnum):
    """The quarantine area a rejected line goes to (system overview §6)."""

    SCHEMA_ERROR = "schema_errors"
    BUSINESS_RULE_FAILURE = "business_rule_failures"


@dataclass(frozen=True, slots=True)
class Accepted:
    """A valid line, kept byte for byte as it arrived."""

    record_id: str
    domain: str
    entity: str
    event_date: date
    schema_version: int
    line: str


@dataclass(frozen=True, slots=True)
class Rejected:
    """A line that goes to quarantine, with why."""

    kind: RejectionKind
    reason: str
    line: str


_EVENT_ADAPTER: Final = TypeAdapter(Event)

_MARKET_DATA_EVENTS: Final = frozenset(
    {EventType.PRICE_UPDATED, EventType.FX_RATE_PUBLISHED, EventType.DIVIDEND_DECLARED}
)
"""Market and issuer reference data; every other event is a brokerage event."""

EXTERNAL_DOMAINS: Final[Mapping[str, str]] = {
    "clearing_confirmations": "clearing",
    "settlement_confirmations": "clearing",
    "fee_records": "clearing",
    "bank_statement_lines": "bank",
    "fx_confirmations": "bank",
    "custody_snapshots": "custody",
    "price_feed": "market_data",
    "price_history": "market_data",
    "fx_rate_history": "market_data",
}
"""The raw-layer domain of each external record set (system overview §6)."""

INGESTED_RECORD_MODELS: Final[Mapping[str, type[BaseModel]]] = {**RECORD_MODELS, **HISTORY_MODELS}
"""Every record set ingested from a dataset's ``external/`` directory: the sets derived
from the events, and the synthetic market history before the window (roadmap 5A)."""

_EXTERNAL_DATES: Final[Mapping[str, Callable[[Any], date]]] = {
    "clearing_confirmations": lambda r: r.trade_date,
    "settlement_confirmations": lambda r: r.actual_settlement_date,
    "fee_records": lambda r: r.trade_date,
    "bank_statement_lines": lambda r: r.value_date,
    "fx_confirmations": lambda r: r.trade_date,
    "custody_snapshots": lambda r: r.snapshot_date,
    "price_feed": lambda r: r.price_date,
    "price_history": lambda r: r.price_date,
    "fx_rate_history": lambda r: r.rate_date,
}
"""The business date each external record is partitioned by."""


def supported_version(model: type[BaseModel]) -> int:
    """The newest ``schema_version`` the code reads for ``model``."""
    default = model.model_fields["schema_version"].default
    if not isinstance(default, int):  # pragma: no cover - every model declares one
        msg = f"{model.__name__} has no integer schema_version default"
        raise TypeError(msg)
    return default


def classify_event(line: str, calendars: Mapping[str, MarketCalendar]) -> Accepted | Rejected:
    """Validate one canonical event line."""
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as error:
        return Rejected(RejectionKind.SCHEMA_ERROR, f"not JSON: {error.msg}", line)
    if not isinstance(raw, dict):
        return Rejected(RejectionKind.SCHEMA_ERROR, "not a JSON object", line)
    name = raw.get("event_type")
    if name not in {str(t) for t in EventType}:
        return Rejected(RejectionKind.SCHEMA_ERROR, f"unknown event_type {name!r}", line)
    event_type = EventType(name)
    version, newest = raw.get("schema_version"), supported_version(EVENT_MODELS[event_type])
    if isinstance(version, int) and version > newest:
        reason = f"{event_type} schema_version {version} is newer than supported ({newest})"
        return Rejected(RejectionKind.SCHEMA_ERROR, reason, line)
    try:
        event = _EVENT_ADAPTER.validate_json(line)
    except ValidationError as error:
        return Rejected(RejectionKind.SCHEMA_ERROR, _first_error(error), line)
    if isinstance(event, TradeExecuted):
        calendar = calendars.get(event.payload.market)
        if calendar is None:
            reason = f"no calendar for market {event.payload.market}"
            return Rejected(RejectionKind.BUSINESS_RULE_FAILURE, reason, line)
        if not calendar.is_trading_day(event.payload.trade_date):
            reason = (
                f"execution on {event.payload.trade_date}, not a {event.payload.market} trading day"
            )
            return Rejected(RejectionKind.BUSINESS_RULE_FAILURE, reason, line)
    domain = "market_data" if event.event_type in _MARKET_DATA_EVENTS else "brokerage"
    return Accepted(
        record_id=event.event_id,
        domain=domain,
        entity=str(event.event_type),
        event_date=business_date(event.event_timestamp),
        schema_version=event.schema_version,
        line=line,
    )


def classify_record(record_set: str, line: str) -> Accepted | Rejected:
    """Validate one external record line of ``record_set``.

    Raises:
        KeyError: ``record_set`` is not a known external record set.
    """
    model = INGESTED_RECORD_MODELS[record_set]
    try:
        record = model.model_validate_json(line)
    except ValidationError as error:
        return Rejected(RejectionKind.SCHEMA_ERROR, _first_error(error), line)
    return Accepted(
        record_id=record.record_id,  # type: ignore[attr-defined]
        domain=EXTERNAL_DOMAINS[record_set],
        entity=record_set,
        event_date=_EXTERNAL_DATES[record_set](record),
        schema_version=EXTERNAL_RECORD_SCHEMA_VERSION,
        line=line,
    )


def _first_error(error: ValidationError) -> str:
    first = error.errors()[0]
    location = ".".join(str(p) for p in first["loc"]) or "<root>"
    return f"invalid {location}: {first['msg']} ({error.error_count()} error(s))"
