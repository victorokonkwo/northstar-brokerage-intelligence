"""Command-line entry point: ``northstar <command>``.

Commands:

- ``run-slice``: simulate the scenario, write the events file, post the
  journal, and load both into the warehouse (needs ``make up``).
- ``ledger-summary``: print the KPI-01 control mart built by dbt. Exits 1 if
  any row reports an unbalanced batch, or if the mart is empty.
- ``ledger-fingerprint``: print a SHA-256 of the stored journal's content,
  to compare two runs.

Connection settings come from the ``NBI_PG_*`` environment variables.
"""

import argparse
import os
import sys
import uuid
from collections.abc import Sequence
from pathlib import Path

from northstar.application.sprint1 import (
    journal_fingerprint,
    load_slice,
    prepare_slice,
    read_ledger_controls,
)
from northstar.infrastructure.postgres import PostgresSettings, WarehouseSchemas


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for every command."""
    parser = argparse.ArgumentParser(prog="northstar", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run-slice", help="simulate, post, and load the Sprint 1 slice")
    run.add_argument("--scenario", type=Path, required=True, help="scenario YAML file")
    run.add_argument("--seed", type=int, required=True, help="dataset seed (non-negative)")
    run.add_argument("--out", type=Path, required=True, help="directory for the events file")
    run.add_argument("--config", type=Path, default=Path("config"), help="policy directory")
    run.add_argument("--run-id", help="ingestion run ID (default: generated)")

    summary = commands.add_parser("ledger-summary", help="print the KPI-01 control mart")
    summary.add_argument(
        "--dbt-schema",
        default=os.environ.get("NBI_DBT_SCHEMA", "dbt_dev"),
        help="schema dbt builds into (default: $NBI_DBT_SCHEMA or dbt_dev)",
    )

    commands.add_parser("ledger-fingerprint", help="print a SHA-256 of the stored journal")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a command and return the process exit code."""
    args = build_parser().parse_args(argv)
    settings = PostgresSettings.from_env()
    schemas = WarehouseSchemas()
    out = sys.stdout

    if args.command == "run-slice":
        prepared = prepare_slice(args.config, args.scenario, args.seed, args.out)
        run_id = args.run_id or f"{prepared.scenario_id}-s{args.seed}-{uuid.uuid4().hex[:12]}"
        with settings.connect() as conn:
            counts = load_slice(conn, prepared, run_id, schemas)
        out.write(
            f"scenario {prepared.scenario_id} seed {args.seed}: {len(prepared.events)} events, "
            f"{len(prepared.batches)} journal batches\n"
            f"events file {prepared.events_path} sha256 {prepared.events_sha256}\n"
            f"loaded (run {run_id}): {counts.events_inserted} new events, "
            f"{counts.lines_inserted} new journal lines\n"
        )
        return 0

    with settings.connect() as conn:
        if args.command == "ledger-fingerprint":
            out.write(f"journal sha256 {journal_fingerprint(conn, schemas)}\n")
            return 0
        rows = read_ledger_controls(conn, args.dbt_schema)

    out.write("business_date currency batches lines total_debit total_credit unbalanced(KPI-01)\n")
    for row in rows:
        out.write(
            f"{row.business_date} {row.currency} {row.batch_count} {row.line_count} "
            f"{row.total_debit} {row.total_credit} {row.unbalanced_batch_count}\n"
        )
    if not rows:
        out.write("no ledger control rows: run `make dbt-build` first\n")
        return 1
    if any(row.unbalanced_batch_count for row in rows):
        out.write("KPI-01 FAILED: unbalanced journal batches (see the rows above)\n")
        return 1
    out.write("KPI-01 passed: every journal batch balances\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
