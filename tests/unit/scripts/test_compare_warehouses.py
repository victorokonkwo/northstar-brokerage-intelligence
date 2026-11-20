"""Normalisation in the Postgres-to-Redshift comparison changes types, never values."""

import importlib.util
import uuid
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).parents[3] / "scripts" / "compare_warehouses.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("compare_warehouses", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_driver_type_differences_compare_equal() -> None:
    n = _module().normalise
    the_id = uuid.uuid5(uuid.NAMESPACE_URL, "x")
    assert n(the_id) == n(str(the_id))
    assert n(["a", "b"]) == n('["a","b"]')
    assert n({"b": 1, "a": 2}) == n('{"a":2,"b":1}')
    assert n("CAD ") == n("CAD")
    toronto = timezone(timedelta(hours=-5))
    assert n(datetime(2026, 3, 2, 9, 30, tzinfo=toronto)) == n(
        datetime(2026, 3, 2, 14, 30, tzinfo=UTC)
    )


def test_values_are_never_rounded_or_coerced() -> None:
    n = _module().normalise
    assert n(Decimal("47329.51")) != n(Decimal("47330"))
    assert n(Decimal("0.166667")) != n(Decimal("0.166666"))
    assert n("[not json") == "[not json"
    assert n(1) != n("1")
