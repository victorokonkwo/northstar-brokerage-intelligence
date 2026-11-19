"""The snapshot content hash depends on the data only."""

from datetime import date
from decimal import Decimal

from northstar.application.publish import content_hash

ROWS: dict[str, list[tuple[object, ...]]] = {
    "mart_a": [(date(2026, 3, 2), "acc_1", Decimal("100.00")), (date(2026, 3, 2), "acc_2", None)],
    "mart_b": [(date(2026, 3, 2), "CAD", Decimal("4.95"))],
}


def test_identical_content_gives_identical_hashes() -> None:
    assert content_hash(ROWS, 0) == content_hash(dict(reversed(ROWS.items())), 0)


def test_a_changed_value_changes_its_mart_and_the_total_only() -> None:
    changed: dict[str, list[tuple[object, ...]]] = {
        **ROWS,
        "mart_b": [(date(2026, 3, 2), "CAD", Decimal("4.96"))],
    }
    overall, per_mart = content_hash(ROWS, 0)
    overall2, per_mart2 = content_hash(changed, 0)
    assert overall != overall2
    assert per_mart["mart_a"] == per_mart2["mart_a"]
    assert per_mart["mart_b"] != per_mart2["mart_b"]


def test_decimal_scale_and_kpi_01_are_part_of_the_content() -> None:
    rescaled: dict[str, list[tuple[object, ...]]] = {
        **ROWS,
        "mart_b": [(date(2026, 3, 2), "CAD", Decimal("4.950"))],
    }
    assert content_hash(ROWS, 0)[0] != content_hash(rescaled, 0)[0]
    assert content_hash(ROWS, 0)[0] != content_hash(ROWS, 1)[0]
