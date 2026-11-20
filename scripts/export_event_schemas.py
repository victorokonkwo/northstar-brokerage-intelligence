"""Write the JSON Schemas of canonical events (``contracts/events/``) and external records.

The pydantic models in ``northstar.domain.events`` are the source of truth.
``tests/contract/test_event_schemas.py`` fails if the committed files differ
from what this script would write, so run it after changing an event model.

Usage: ``uv run python scripts/export_event_schemas.py [OUTPUT_DIR]``.
"""

import json
import sys
from pathlib import Path

from northstar.domain.events import EVENT_MODELS
from northstar.simulation.defects import DefectEntry
from northstar.simulation.external import EXTERNAL_RECORD_SCHEMA_VERSION, RECORD_MODELS
from northstar.simulation.history import HISTORY_MODELS

DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "contracts" / "events"
EXTERNAL_OUTPUT = Path(__file__).resolve().parents[1] / "contracts" / "external"


def render_schemas() -> dict[str, str]:
    """Return ``{file name: file content}`` for every event type."""
    return {
        f"{event_type}.v{model.model_fields['schema_version'].default}.schema.json": json.dumps(
            model.model_json_schema(mode="validation"), indent=2, sort_keys=True
        )
        + "\n"
        for event_type, model in EVENT_MODELS.items()
    }


def render_external_schemas() -> dict[str, str]:
    """Return ``{file name: content}`` for each external record set and the defect manifest."""
    models = dict(RECORD_MODELS) | dict(HISTORY_MODELS) | {"defect_manifest": DefectEntry}
    return {
        f"{name}.v{EXTERNAL_RECORD_SCHEMA_VERSION}.schema.json": json.dumps(
            model.model_json_schema(mode="validation"), indent=2, sort_keys=True
        )
        + "\n"
        for name, model in models.items()
    }


def main(argv: list[str]) -> int:
    """Write the schemas; return the process exit code."""
    output = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUTPUT
    output.mkdir(parents=True, exist_ok=True)
    for name, content in render_schemas().items():
        (output / name).write_text(content, encoding="utf-8")
        sys.stdout.write(f"wrote {output / name}\n")
    if len(argv) <= 1:  # the default layout also writes the external contracts
        EXTERNAL_OUTPUT.mkdir(parents=True, exist_ok=True)
        for name, content in render_external_schemas().items():
            (EXTERNAL_OUTPUT / name).write_text(content, encoding="utf-8")
            sys.stdout.write(f"wrote {EXTERNAL_OUTPUT / name}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
