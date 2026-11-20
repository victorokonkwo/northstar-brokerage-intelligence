"""Compare every dbt model in two schemas, row for row, on one warehouse or across two.

By default it compares a schema on local Postgres with the same schema on
Redshift. ``--right postgres --right-schema dbt_dataset_full`` instead compares
an incremental build with a full-refresh build of the same data (roadmap 4F-b).

Each model's rows are compared as multisets, leaving out the lineage columns
that identify a run rather than the data (``_ingestion_run_id``,
``_ingested_at``, ``_dbt_invocation_id``, ``_build_seq``). The build log tables
(``dbt_build_log``, ``dbt_model_builds``) record builds, not data, and are
skipped, as is Redshift's empty ``lifecycle_lock``. Values are normalised only
where the two drivers return the same value as different Python types: a
``uuid`` as an object or a string, a JSON value as a list or as JSON text, and
``char(n)`` padding. Amounts are compared exactly.

Usage (from the repository root; Redshift needs ``aws login``)::

    set -a && . ./.env && set +a
    eval "$(aws configure export-credentials --format env)"
    eval "$(python scripts/redshift_env.py)"
    python scripts/compare_warehouses.py --schema dbt_dataset

Exits 0 when every model is identical, 1 otherwise.
"""

import argparse
import json
import sys
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import sql

from northstar.infrastructure.postgres import PostgresSettings
from northstar.infrastructure.redshift import RedshiftSettings

LINEAGE: frozenset[str] = frozenset(
    {"_dbt_invocation_id", "_ingestion_run_id", "_ingested_at", "_build_seq"}
)
BUILD_LOG_TABLES: frozenset[str] = frozenset({"dbt_build_log", "dbt_model_builds"})
# Redshift's stand-in for SELECT ... FOR UPDATE: an empty table that exists there only.
WAREHOUSE_SPECIFIC_TABLES: frozenset[str] = frozenset({"lifecycle_lock"})
WAREHOUSES = ("postgres", "redshift")


def normalise(value: Any) -> Any:
    """One value in a form both drivers agree on (see the module docstring)."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(UTC)
    if isinstance(value, list | dict):
        return json.dumps(value, sort_keys=True)
    if isinstance(value, str) and value[:1] in "[{":
        try:
            return json.dumps(json.loads(value), sort_keys=True)  # SUPER comes back as text
        except json.JSONDecodeError:
            return value
    if isinstance(value, str):
        return value.rstrip(" ")  # char(n) padding
    return value


def model_rows(
    conn: psycopg.Connection[Any], schema: str, table: str, columns: Sequence[str]
) -> Counter[tuple[Any, ...]]:
    """The model's rows over ``columns``, normalised, as a multiset."""
    query = sql.SQL("select {} from {}").format(
        sql.SQL(", ").join(map(sql.Identifier, columns)), sql.Identifier(schema, table)
    )
    return Counter(tuple(normalise(v) for v in row) for row in conn.execute(query).fetchall())


def _connect(warehouse: str) -> psycopg.Connection[Any]:
    if warehouse == "redshift":
        return RedshiftSettings.from_env().connect()
    return PostgresSettings.from_env().connect()


def _tables(conn: psycopg.Connection[Any], schema: str) -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            "select table_name from information_schema.tables where table_schema = %s order by 1",
            (schema,),
        ).fetchall()
        if r[0] not in BUILD_LOG_TABLES | WAREHOUSE_SPECIFIC_TABLES
    ]


def compare(
    schema: str,
    *,
    left: str = "postgres",
    right: str = "redshift",
    right_schema: str | None = None,
    ignore: frozenset[str] = frozenset(),
) -> list[str]:
    """One line per table that differs (row counts on each side); empty when all match.

    ``left`` and ``right`` name the warehouses; ``right_schema`` defaults to
    ``schema``. ``ignore`` adds columns to leave out, beyond the lineage ones.
    """
    right_schema = right_schema or schema
    names = (f"{left}:{schema}", f"{right}:{right_schema}")
    differences: list[str] = []
    with _connect(left) as lc, _connect(right) as rc:
        tables = _tables(lc, schema)
        missing = sorted(set(tables) ^ set(_tables(rc, right_schema)))
        differences += [f"{t}: in only one of {names[0]} and {names[1]}" for t in missing]
        for table in (t for t in tables if t not in missing):
            columns = [
                str(r[0])
                for r in lc.execute(
                    "select column_name from information_schema.columns "
                    "where table_schema = %s and table_name = %s order by ordinal_position",
                    (schema, table),
                ).fetchall()
                if r[0] not in LINEAGE | ignore
            ]
            ours = model_rows(lc, schema, table, columns)
            theirs = model_rows(rc, right_schema, table, columns)
            if ours != theirs:
                differences.append(
                    f"{table}: {sum(ours.values())} rows in {names[0]}, "
                    f"{sum(theirs.values())} in {names[1]}; "
                    f"{sum((ours - theirs).values())} only in {names[0]}, "
                    f"{sum((theirs - ours).values())} only in {names[1]}"
                )
        sys.stdout.write(
            f"{len(tables) - len(differences)} of {len(tables)} tables identical "
            f"({names[0]} vs {names[1]})\n"
        )
    return differences


def main(argv: Sequence[str] | None = None) -> int:
    """Compare and report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--schema", required=True, help="the dbt schema (on both sides by default)")
    parser.add_argument("--left", choices=WAREHOUSES, default="postgres")
    parser.add_argument("--right", choices=WAREHOUSES, default="redshift")
    parser.add_argument("--right-schema", help="the schema on the right (default: --schema)")
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="a column to leave out too, such as one that records when a run happened",
    )
    args = parser.parse_args(argv)
    differences = compare(
        args.schema,
        left=args.left,
        right=args.right,
        right_schema=args.right_schema,
        ignore=frozenset(args.ignore),
    )
    for line in differences:
        sys.stdout.write(f"  differs: {line}\n")
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main())
