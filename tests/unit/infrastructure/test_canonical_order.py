"""Snapshot rows sort the same way whatever warehouse returned them (4F-c)."""

from datetime import date
from decimal import Decimal

from hypothesis import given
from hypothesis import strategies as st

from northstar.application.publish import content_hash
from northstar.infrastructure.ordering import canonical_order


def test_columns_sort_left_to_right_by_value_with_nulls_last() -> None:
    rows = [
        (date(2026, 3, 3), "acc_1", Decimal("1.00")),
        (date(2026, 3, 2), "acc_2", None),
        (date(2026, 3, 2), "acc_2", Decimal("10.00")),
        (date(2026, 3, 2), "acc_2", Decimal("9.50")),
        (date(2026, 3, 2), "acc_1", Decimal("5.00")),
    ]
    assert canonical_order(rows) == [
        (date(2026, 3, 2), "acc_1", Decimal("5.00")),
        (date(2026, 3, 2), "acc_2", Decimal("9.50")),  # numbers by value, not as text
        (date(2026, 3, 2), "acc_2", Decimal("10.00")),
        (date(2026, 3, 2), "acc_2", None),  # nulls last, as in SQL ascending
        (date(2026, 3, 3), "acc_1", Decimal("1.00")),
    ]


def test_text_sorts_by_code_point_not_by_a_locale() -> None:
    # A locale such as en_US puts lower case before upper and can ignore
    # punctuation; code-point order does neither, so no collation can change it.
    rows = [("b",), ("a_b",), ("B",), ("ab",), ("a",)]
    assert canonical_order(rows) == [("B",), ("a",), ("a_b",), ("ab",), ("b",)]


def test_equal_values_written_differently_still_have_one_order() -> None:
    one, other = (Decimal("1.0"),), (Decimal("1.00"),)
    assert canonical_order([one, other]) == canonical_order([other, one])


@given(
    st.lists(
        st.tuples(
            st.sampled_from(["CAD", "USD", "acc_1", "Acc_1"]),
            st.none() | st.decimals(min_value=-1000, max_value=1000, allow_nan=False),
        ),
        max_size=20,
    ),
    st.data(),
)
def test_any_arrival_order_gives_the_same_hash(
    rows: list[tuple[str, Decimal | None]], data: st.DataObject
) -> None:
    shuffled = data.draw(st.permutations(rows))
    assert content_hash({"m": canonical_order(rows)}, 0) == content_hash(
        {"m": canonical_order(shuffled)}, 0
    )
