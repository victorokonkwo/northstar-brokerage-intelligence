"""Drop the parity run's schemas on one warehouse, so ``make parity`` starts from nothing.

On Redshift the admin owns the append-only tables, so the schemas are dropped
as the admin (``NBI_RS_ADMIN_SECRET_ARN``, from ``scripts/redshift_env.py``).
Only the fixed parity schema names below can be dropped.

Usage (from the repository root)::

    python scripts/parity_reset.py --warehouse postgres
"""

import argparse
import os
import sys
from collections.abc import Sequence
from typing import Any, Final

import psycopg
from psycopg import sql

from northstar.infrastructure.postgres import PostgresSettings
from northstar.infrastructure.redshift import RedshiftSettings

SCHEMAS: Final = (
    "raw_parity",
    "ledger_parity",
    "recon_parity",
    "dbt_parity",
    "published_parity",
    "published_controls_parity",
)


def main(argv: Sequence[str] | None = None) -> int:
    """Drop every parity schema on the chosen warehouse."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--warehouse", choices=("postgres", "redshift"), required=True)
    args = parser.parse_args(argv)
    conn: psycopg.Connection[Any] = (
        RedshiftSettings.from_env().connect_admin(os.environ["NBI_RS_ADMIN_SECRET_ARN"])
        if args.warehouse == "redshift"
        else PostgresSettings.from_env().connect()
    )
    with conn:
        for schema in SCHEMAS:
            conn.execute(sql.SQL("drop schema if exists {} cascade").format(sql.Identifier(schema)))
    sys.stdout.write(f"dropped {', '.join(SCHEMAS)} on {args.warehouse}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
