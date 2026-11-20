"""The dashboard check reports any difference in rows, counting duplicates."""

import importlib.util
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import ModuleType

SCRIPT = Path(__file__).parents[3] / "scripts" / "dashboard_check.py"


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dashboard_check", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ROW = (date(2026, 3, 2), "CAD", Decimal("0.944444"))


def test_equal_rows_are_no_difference() -> None:
    assert _module().differences(Counter([ROW]), Counter([ROW]), "the mart") == []


def test_a_changed_value_a_missing_row_and_a_duplicate_are_differences() -> None:
    d = _module().differences
    other = (date(2026, 3, 2), "CAD", Decimal("0.944400"))
    assert d(Counter([ROW]), Counter([other]), "the mart") == [
        "1 rows shown, 1 in the mart; 1 only shown, 1 only in the mart"
    ]
    assert d(Counter(), Counter([ROW]), "the mart")
    assert d(Counter([ROW, ROW]), Counter([ROW]), "the mart")
