"""A row order that no warehouse collation can change (roadmap 4F-c)."""

from collections.abc import Iterable, Sequence
from typing import Any


def canonical_order(rows: Iterable[Sequence[Any]]) -> list[tuple[Any, ...]]:
    """Rows sorted by every column ascending, nulls last, compared as Python values.

    This is SQL's ``order by <every column>`` without a collation: text compares
    by code point, so the order is the same on every warehouse (Postgres's
    ``en_US.utf8`` and Redshift sort text differently). Snapshot content hashes
    and reconciliation inputs both rely on it. On every snapshot stored locally
    on 2026-11-20 it gave the same order as Postgres did, so no stored hash
    changed.
    """
    return sorted(
        (tuple(row) for row in rows),
        # str(v) breaks ties between equal values written differently (1.0 and 1.00).
        key=lambda row: tuple((v is None, v, str(v)) for v in row),
    )
