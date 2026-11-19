r"""Canonical JSON Lines files for events.

The byte format is fixed so that the same events always produce the same
file (FR-SIM-01): one JSON object per line, keys sorted, no insignificant
whitespace, UTF-8, ``\n`` line endings, and decimals as fixed-point strings.
"""

import json
from collections.abc import Iterable
from pathlib import Path

from pydantic import TypeAdapter

from northstar.domain.events import Event

_EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)


def serialize_event(event: Event) -> str:
    """Return the canonical one-line JSON form of ``event``, without a newline."""
    return json.dumps(
        _EVENT_ADAPTER.dump_python(event, mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def write_events(path: Path, events: Iterable[Event]) -> int:
    """Write ``events`` to ``path`` as canonical JSON Lines, replacing the file.

    Returns:
        The number of events written.
    """
    lines = [serialize_event(event) + "\n" for event in events]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        file.writelines(lines)
    return len(lines)


def read_events(path: Path) -> tuple[Event, ...]:
    """Read and validate every event in a JSON Lines file.

    Raises:
        pydantic.ValidationError: A line is not a valid event. The error
            names the failing field; the line number is in the message.
    """
    events = []
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            try:
                events.append(_EVENT_ADAPTER.validate_json(line))
            except ValueError as error:
                msg = f"{path}:{line_number}: invalid event"
                raise ValueError(msg) from error
    return tuple(events)
