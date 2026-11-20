"""Check the operations dashboard's data against the published snapshots and the marts.

FR-M2-09 asks that the dashboard reads only published marts and that its figures
equal the mart query results. The dashboard reads the serving views and
calculates nothing (docs/dashboards/operations.md), so this checks, for each
view:

1. It shows exactly the currently PUBLISHED version of every published date,
   with that version's number and publication time (FR-DASH-01).
2. Its rows equal the published copy of that version, and the dbt mart's rows
   for the same dates, row for row.

Usage: ``make dashboard-check``, after ``make publish`` (it passes ``--dbt-schema``
and ``--published-controls-schema``; add ``--warehouse redshift`` for the cloud).

Exits 0 when every view checks out, 1 otherwise.
"""

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from typing import Any, Final

import psycopg
from psycopg import sql

from northstar.infrastructure.postgres import PostgresSettings
from northstar.infrastructure.redshift import RedshiftSettings
from northstar.infrastructure.snapshots import LINEAGE_ONLY_COLUMNS

VIEWS: Final = {
    "serving_match_rates": "mart_match_rates",
    "serving_exception_monitoring": "mart_exception_monitoring",
    "serving_settlement_fails": "mart_settlement_fails",
    "serving_cash_breaks": "mart_cash_breaks",
}
SNAPSHOT_COLUMNS: Final = ("snapshot_version", "published_at")


def differences(
    shown: Counter[tuple[Any, ...]], expected: Counter[tuple[Any, ...]], against: str
) -> list[str]:
    """One line describing how ``shown`` and ``expected`` differ, or none if they are equal."""
    if shown == expected:
        return []
    return [
        f"{sum(shown.values())} rows shown, {sum(expected.values())} in {against}; "
        f"{sum((shown - expected).values())} only shown, "
        f"{sum((expected - shown).values())} only in {against}"
    ]


def _rows(
    conn: psycopg.Connection[Any], query: sql.Composed, params: Sequence[Any] = ()
) -> Counter[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        return Counter(tuple(row) for row in cur.fetchall())


def _columns(conn: psycopg.Connection[Any], schema: str, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "select column_name from information_schema.columns "
            "where table_schema = %s and table_name = %s order by ordinal_position",
            (schema, table),
        )
        return [str(r[0]) for r in cur.fetchall()]


def check(conn: psycopg.Connection[Any], dbt_schema: str, published: str) -> list[str]:
    """One line per problem found; empty when every view checks out."""
    serving = f"{dbt_schema}_serving"
    timeline = sql.Identifier(published, "snapshot_timeline")
    current = set(
        _rows(
            conn,
            sql.SQL(
                "select business_date, snapshot_version, published_at from {} "
                "where status = 'PUBLISHED'"
            ).format(timeline),
        )
    )
    dates = {row[0] for row in current}
    problems: list[str] = []
    for view, mart in VIEWS.items():
        content = [
            c
            for c in _columns(conn, published, mart)
            if c not in LINEAGE_ONLY_COLUMNS and c != "snapshot_version"
        ]
        cols = sql.SQL(", ").join(map(sql.Identifier, content))
        shown_with_version = _rows(
            conn,
            sql.SQL("select {}, snapshot_version, published_at from {}").format(
                cols, sql.Identifier(serving, view)
            ),
        )
        date_at = content.index("business_date")
        found: list[str] = []
        # 1. Only the published version of a date, with its number and time.
        versions = {(row[date_at], *row[len(content) :]) for row in shown_with_version}
        if not versions <= current:
            found.append("shows a version that is not the one currently published")
        shown: Counter[tuple[Any, ...]] = Counter()
        for row, n in shown_with_version.items():
            shown[row[: len(content)]] += n
        # 2. Exactly the published copy of those versions (so every date is covered)...
        published_rows = _rows(
            conn,
            sql.SQL(
                "select {} from {} as m join {} as t on m.business_date = t.business_date "
                "and m.snapshot_version = t.snapshot_version where t.status = 'PUBLISHED'"
            ).format(
                sql.SQL(", ").join(sql.Identifier("m", c) for c in content),
                sql.Identifier(published, mart),
                timeline,
            ),
        )
        found += differences(shown, published_rows, "the published snapshot")
        # ... and the dbt mart's rows for the published dates (FR-M2-09).
        mart_rows = Counter(
            {
                row: n
                for row, n in _rows(
                    conn,
                    sql.SQL("select {} from {}").format(cols, sql.Identifier(dbt_schema, mart)),
                ).items()
                if row[date_at] in dates
            }
        )
        found += differences(shown, mart_rows, mart)
        verdict = "DIFFERS" if found else "equal to the published snapshot and the mart"
        sys.stdout.write(
            f"{view}: {sum(shown.values())} rows, {len(versions)} date versions; {verdict}\n"
        )
        problems += [f"{view}: {line}" for line in found]
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    """Check and report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dbt-schema", required=True, help="schema dbt built the marts in")
    parser.add_argument("--published-controls-schema", required=True)
    parser.add_argument("--warehouse", choices=("postgres", "redshift"), default="postgres")
    args = parser.parse_args(argv)
    settings = (
        RedshiftSettings.from_env() if args.warehouse == "redshift" else PostgresSettings.from_env()
    )
    with settings.connect() as conn:
        problems = check(conn, args.dbt_schema, args.published_controls_schema)
    for line in problems:
        sys.stdout.write(f"  problem: {line}\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
