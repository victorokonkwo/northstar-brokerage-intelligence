"""Compare every dbt model in one schema between local Postgres and Redshift, row for row.

Each model's rows are compared as multisets, leaving out the lineage columns
that identify a run rather than the data (``_ingestion_run_id``,
``_ingested_at``, ``_dbt_invocation_id``). Values are normalised only where
the two drivers return the same value as different Python types: a ``uuid``
as an object or a string, a JSON value as a list or as JSON text, and
``char(n)`` padding. Amounts are compared exactly.

Usage (from the repository root, after ``aws login``)::

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

LINEAGE: frozenset[str] = frozenset({"_dbt_invocation_id", "_ingestion_run_id", "_ingested_at"})


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


def compare(schema: str) -> list[str]:
    """One line per model that differs (row counts on each side); empty when all match."""
    differences: list[str] = []
    with PostgresSettings.from_env().connect() as pg, RedshiftSettings.from_env().connect() as rs:
        tables = [
            str(r[0])
            for r in pg.execute(
                "select table_name from information_schema.tables where table_schema = %s "
                "order by 1",
                (schema,),
            ).fetchall()
        ]
        for table in tables:
            columns = [
                str(r[0])
                for r in pg.execute(
                    "select column_name from information_schema.columns "
                    "where table_schema = %s and table_name = %s order by ordinal_position",
                    (schema, table),
                ).fetchall()
                if r[0] not in LINEAGE
            ]
            local, cloud = (
                model_rows(pg, schema, table, columns),
                model_rows(rs, schema, table, columns),
            )
            if local != cloud:
                differences.append(
                    f"{table}: {sum(local.values())} rows in Postgres, {sum(cloud.values())} in "
                    f"Redshift; {sum((local - cloud).values())} only in Postgres, "
                    f"{sum((cloud - local).values())} only in Redshift"
                )
        sys.stdout.write(f"{len(tables) - len(differences)} of {len(tables)} models identical\n")
    return differences


def main(argv: Sequence[str] | None = None) -> int:
    """Compare and report."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--schema", required=True, help="the dbt schema on both warehouses")
    args = parser.parse_args(argv)
    differences = compare(args.schema)
    for line in differences:
        sys.stdout.write(f"  differs: {line}\n")
    return 1 if differences else 0


if __name__ == "__main__":
    sys.exit(main())
