"""What every warehouse load shares, whichever the dialect.

Loads are idempotent and never overwrite (FR-M1-03, ADR 002): a row whose key
is already stored is skipped if identical and refused if not.
"""

from collections.abc import Sequence
from typing import Any

from northstar.common.exceptions import NorthstarError


class LoadConflictError(NorthstarError):
    """Raised when a replayed row has the same key but different content."""


def distinct_rows(
    schema: str, table: str, key: str, rows: Sequence[tuple[Any, ...]]
) -> list[tuple[Any, ...]]:
    """The input's rows, one per key, after checking repeats agree.

    ``rows`` are tuples with the key first and the run ID last; the columns in
    between are the content. A key repeated with different content is a
    corrupted input.

    Raises:
        LoadConflictError: The input repeats a key with different content.
    """
    by_key: dict[Any, tuple[Any, ...]] = {}
    for row in rows:
        previous = by_key.setdefault(row[0], row)
        if previous[1:-1] != row[1:-1]:
            msg = f"{schema}.{table}: the input repeats {key} {row[0]} with different content"
            raise LoadConflictError(msg)
    return list(by_key.values())
