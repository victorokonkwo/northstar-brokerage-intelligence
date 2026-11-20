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


def test_a_view_is_judged_against_its_snapshot_before_the_mart() -> None:
    compare = _module().compare_view
    later = (date(2026, 3, 3), "CAD", Decimal("0.5"))
    moved = (date(2026, 3, 3), "CAD", Decimal("0.6"))
    snapshot = Counter([ROW, later])
    # The view equals its snapshot and the mart: no problem.
    assert compare(snapshot, snapshot, snapshot, 0, "mart_x") == []
    # The view differs from its snapshot: a broken view, whatever the mart says.
    (broken,) = compare(Counter([ROW]), snapshot, Counter([ROW]), 0, "mart_x")
    assert "the published snapshot" in broken
    # The view equals its snapshot, but the mart moved on 2026-03-03: unpublished changes.
    (unpublished,) = compare(snapshot, snapshot, Counter([ROW, moved]), 0, "mart_x")
    assert unpublished == (
        "unpublished changes: mart_x differs from the published snapshot on 1 date(s) "
        "from 2026-03-03; publish the controls stream"
    )
