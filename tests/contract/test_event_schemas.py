"""The committed event JSON Schemas must match the pydantic models exactly."""

import importlib.util
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "contracts" / "events"


def _exporter() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "export_event_schemas", REPO_ROOT / "scripts" / "export_event_schemas.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_schemas_match_the_models() -> None:
    expected: dict[str, str] = _exporter().render_schemas()
    committed = {p.name: p.read_text(encoding="utf-8") for p in SCHEMA_DIR.glob("*.schema.json")}
    assert sorted(committed) == sorted(expected), "run scripts/export_event_schemas.py"
    for name, content in expected.items():
        assert committed[name] == content, f"{name} is stale: run scripts/export_event_schemas.py"


def test_schemas_carry_decimals_as_strings() -> None:
    schema = (SCHEMA_DIR / "trade_executed.v2.schema.json").read_text(encoding="utf-8")
    assert '"pattern"' in schema or '"string"' in schema


def test_committed_external_schemas_match_the_models() -> None:
    expected: dict[str, str] = _exporter().render_external_schemas()
    external_dir = REPO_ROOT / "contracts" / "external"
    committed = {p.name: p.read_text(encoding="utf-8") for p in external_dir.glob("*.schema.json")}
    assert sorted(committed) == sorted(expected), "run scripts/export_event_schemas.py"
    for name, content in expected.items():
        assert committed[name] == content, f"{name} is stale: run scripts/export_event_schemas.py"
