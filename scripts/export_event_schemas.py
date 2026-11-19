"""Write the JSON Schema of every canonical event to ``contracts/events/``.

The pydantic models in ``northstar.domain.events`` are the source of truth.
``tests/contract/test_event_schemas.py`` fails if the committed files differ
from what this script would write, so run it after changing an event model.

Usage: ``uv run python scripts/export_event_schemas.py [OUTPUT_DIR]``.
"""

import json
import sys
from pathlib import Path

from northstar.domain.events import EVENT_MODELS, SCHEMA_VERSION

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "contracts" / "events"


def render_schemas() -> dict[str, str]:
    """Return ``{file name: file content}`` for every event type."""
    return {
        f"{event_type}.v{SCHEMA_VERSION}.schema.json": json.dumps(
            model.model_json_schema(mode="validation"), indent=2, sort_keys=True
        )
        + "\n"
        for event_type, model in EVENT_MODELS.items()
    }


def main(argv: list[str]) -> int:
    """Write the schemas; return the process exit code."""
    output = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUTPUT
    output.mkdir(parents=True, exist_ok=True)
    for name, content in render_schemas().items():
        (output / name).write_text(content, encoding="utf-8")
        sys.stdout.write(f"wrote {output / name}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
