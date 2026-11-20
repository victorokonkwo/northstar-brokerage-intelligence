"""Command-line entry point: ``northstar <command>``.

Commands:

- ``run-slice``: simulate the scenario, write the events file, post the
  journal, and load both into the warehouse (needs ``make up``).
- ``ledger-summary``: print the KPI-01 control mart built by dbt. Exits 1 if
  any row reports an unbalanced batch, or if the mart is empty.
- ``ledger-fingerprint``: print a SHA-256 of the stored journal's content,
  to compare two runs.
- ``simulate``: generate a dataset (canonical events, external records, defect
  manifest) with the seeded generator (no database needed).
- ``ingest``: validate a generated dataset into the object store's raw layer
  and write the run manifest (needs ``make up``).
- ``load-run``: load an ingestion run's raw objects into dataset-specific
  warehouse schemas, posting the events (needs ``make up``).
- ``publish``: publish business dates' marts from a dbt schema as versioned
  snapshots (ADR 006). Exits 2 if a date's gate fails and it stays DRAFT.
- ``snapshot-history``: print every version of one business date.
- ``is-business-day``: exit 0 for a ledger business day, 99 otherwise (the
  orchestrator skips the day's run on 99).
- ``archive-lineage``: store an OpenLineage JSON Lines file under ``audit/lineage/``.
- ``record-alert``: log a failed pipeline task and store it under ``audit/alerts/``.
- ``reconcile``: run the seven reconciliations against a dbt schema and store
  matches and exceptions append-only.
- ``exception transition|history``: move an exception through its lifecycle
  (with an actor and reason), or print its audit trail.
- ``recon-verify``: compare stored exceptions with a defect manifest; exit 1
  unless they agree exactly (NFR-03).

Connection settings come from the ``NBI_PG_*`` and ``NBI_S3_*`` environment
variables.
"""

import argparse
import os
import sys
import uuid
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from importlib.metadata import version
from pathlib import Path

from opentelemetry import trace

from northstar.application.ingest import ingest_dataset, load_run
from northstar.application.lifecycle import transition_exception
from northstar.application.operations import (
    Alert,
    archive_lineage,
    is_ledger_business_day,
    record_alert,
)
from northstar.application.policies import load_policies
from northstar.application.publish import publish_all, publish_date
from northstar.application.reconcile import reconcile_warehouse, verify_against_manifest
from northstar.application.simulate import simulate_dataset
from northstar.application.sprint1 import (
    journal_fingerprint,
    load_slice,
    prepare_slice,
    read_ledger_controls,
)
from northstar.common.exceptions import PublicationBlockedError
from northstar.domain.lifecycle import IllegalTransitionError, Resolution, Status
from northstar.infrastructure.build_info import code_version
from northstar.infrastructure.object_store import ObjectStoreSettings, S3ObjectStore
from northstar.infrastructure.postgres import PostgresSettings, WarehouseSchemas
from northstar.infrastructure.recon_store import history
from northstar.infrastructure.snapshots import history as snapshot_history
from northstar.infrastructure.telemetry import TelemetrySettings, configure_telemetry
from northstar.simulation.engine import LEDGER_CALENDAR_MARKET

NOT_A_BUSINESS_DAY = 99
"""Exit code of ``is-business-day`` for a non-business day (the DAG skips on it)."""


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

    sim = commands.add_parser(
        "simulate", help="generate a dataset's canonical events (no database)"
    )
    sim.add_argument("--dataset", type=Path, required=True, help="dataset config YAML")
    sim.add_argument("--seed", type=int, required=True, help="dataset seed (non-negative)")
    sim.add_argument("--out", type=Path, required=True, help="output directory")
    sim.add_argument("--config", type=Path, default=Path("config"), help="policy directory")
    sim.add_argument("--defects", type=Path, help="defect config YAML (omit for exact records)")

    ing = commands.add_parser("ingest", help="validate a dataset into the raw layer")
    ing.add_argument("--dataset-dir", type=Path, required=True, help="a simulate output root")
    ing.add_argument("--run-id", required=True, help="ingestion run ID (unique per run)")
    ing.add_argument("--config", type=Path, default=Path("config"), help="policy directory")

    load = commands.add_parser("load-run", help="load an ingestion run into the warehouse")
    load.add_argument("--run-id", required=True, help="a successful ingestion run ID")
    load.add_argument("--raw-schema", required=True, help="schema for raw data, e.g. raw_dev")
    load.add_argument(
        "--ledger-schema", required=True, help="schema for the journal, e.g. ledger_dev"
    )
    load.add_argument("--config", type=Path, default=Path("config"), help="policy directory")

    pub = commands.add_parser("publish", help="publish business dates as versioned snapshots")
    pub.add_argument("--dbt-schema", required=True, help="schema dbt built the marts in")
    pub.add_argument("--published-schema", required=True, help="schema of published snapshots")
    pub.add_argument("--business-date", type=date.fromisoformat, help="one date (default: all)")
    pub.add_argument("--note", help="operator note appended to any restatement reason")

    hist = commands.add_parser("snapshot-history", help="print every version of a business date")
    hist.add_argument("--published-schema", required=True, help="schema of published snapshots")
    hist.add_argument("--business-date", type=date.fromisoformat, required=True)

    day = commands.add_parser("is-business-day", help="exit 0 on a ledger business day, else 99")
    day.add_argument("--date", type=date.fromisoformat, required=True)
    day.add_argument("--config", type=Path, default=Path("config"), help="policy directory")

    lin = commands.add_parser("archive-lineage", help="store OpenLineage events in the audit area")
    lin.add_argument("--file", type=Path, required=True, help="OpenLineage JSON Lines file")
    lin.add_argument("--run-id", required=True, help="pipeline run ID (one archive per run)")

    alert = commands.add_parser("record-alert", help="log and store a failed pipeline task")
    alert.add_argument("--dag-id", required=True)
    alert.add_argument("--task-id", required=True)
    alert.add_argument("--run-id", required=True)
    alert.add_argument("--try-number", type=int, required=True)
    alert.add_argument("--message", required=True)

    rec = commands.add_parser("reconcile", help="reconcile a dbt schema and store exceptions")
    rec.add_argument("--dbt-schema", required=True, help="schema dbt built the models in")
    rec.add_argument("--recon-schema", required=True, help="schema for matches and exceptions")
    rec.add_argument("--as-of", type=date.fromisoformat, help="default: last reporting date")
    rec.add_argument("--config", type=Path, default=Path("config"), help="policy directory")

    exc = commands.add_parser("exception", help="change or show an exception's status")
    exc.add_argument("action", choices=["transition", "history"])
    exc.add_argument("--recon-schema", required=True)
    exc.add_argument("--exception-id", required=True)
    exc.add_argument("--to", choices=[s.value for s in Status], help="target status")
    exc.add_argument("--actor", help="user:<name> for a person")
    exc.add_argument("--reason", help="why (required for every change)")
    exc.add_argument("--resolution", choices=[r.value for r in Resolution])

    ver = commands.add_parser("recon-verify", help="compare exceptions with a defect manifest")
    ver.add_argument("--recon-schema", required=True)
    ver.add_argument("--manifest", type=Path, required=True, help="defects/manifest.jsonl")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a command and return the process exit code.

    Telemetry is configured for the whole command and flushed before exit.
    """
    args = build_parser().parse_args(argv)
    telemetry = configure_telemetry(TelemetrySettings.from_env(), version("northstar"))
    try:
        with trace.get_tracer(__name__).start_as_current_span(f"northstar.{args.command}"):
            return _run(args)
    finally:
        telemetry.shutdown()


def _run(args: argparse.Namespace) -> int:
    out = sys.stdout
    if args.command == "simulate":
        result = simulate_dataset(args.config, args.dataset, args.seed, args.out, args.defects)
        out.write(
            f"dataset {result.dataset_id} seed {result.seed}: "
            f"{sum(result.counts.values())} events under {result.root}\n"
        )
        for event_type, count in result.counts.items():
            out.write(f"  event {event_type} {count}\n")
        for name, count in result.external_counts.items():
            out.write(f"  external {name} {count}\n")
        for defect_type, count in result.defect_counts.items():
            out.write(f"  defect {defect_type} {count}\n")
        for name, digest in result.file_sha256.items():
            out.write(f"  sha256 {digest} {name}\n")
        return 0
    if args.command == "is-business-day":
        ledger = load_policies(args.config).calendars[LEDGER_CALENDAR_MARKET]
        business = is_ledger_business_day(ledger, args.date)
        out.write(f"{args.date} is {'' if business else 'not '}a ledger business day\n")
        return 0 if business else NOT_A_BUSINESS_DAY
    if args.command == "archive-lineage":
        key = archive_lineage(S3ObjectStore(ObjectStoreSettings.from_env()), args.file, args.run_id)
        out.write(f"archived lineage to {key}\n")
        return 0
    if args.command == "record-alert":
        alert_key = record_alert(
            S3ObjectStore(ObjectStoreSettings.from_env()),
            Alert(
                args.dag_id,
                args.task_id,
                args.run_id,
                args.try_number,
                args.message,
                datetime.now(UTC),
            ),
        )
        out.write(f"recorded alert at {alert_key}\n")
        return 0
    if args.command == "ingest":
        store = S3ObjectStore(ObjectStoreSettings.from_env())
        manifest = ingest_dataset(store, args.config, args.dataset_dir, args.run_id, code_version())
        raw = manifest.raw_objects()
        out.write(
            f"run {manifest.run_id} {manifest.status}: "
            f"{sum(f.accepted for f in manifest.files)} accepted, "
            f"{sum(f.duplicates for f in manifest.files)} duplicate, "
            f"{sum(f.quarantined for f in manifest.files)} quarantined lines; "
            f"{len(raw)} raw objects ({sum(o.written for o in raw)} new)\n"
        )
        return 0
    settings = PostgresSettings.from_env()
    schemas = WarehouseSchemas()
    if args.command == "load-run":
        store = S3ObjectStore(ObjectStoreSettings.from_env())
        dataset_schemas = WarehouseSchemas(raw=args.raw_schema, ledger=args.ledger_schema)
        with settings.connect() as conn:
            loaded = load_run(conn, store, args.config, args.run_id, dataset_schemas)
        out.write(
            f"run {args.run_id}: read {loaded.events_read} events and {loaded.records_read} "
            f"external records, posted {loaded.batches_posted} batches; loaded into "
            f"{args.raw_schema}/{args.ledger_schema}: {loaded.events_inserted} new events, "
            f"{loaded.lines_inserted} new journal lines, "
            f"{loaded.records_inserted} new external records\n"
        )
        return 0

    if args.command == "publish":
        run_id = f"publish-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC)
        try:
            with settings.connect() as conn:
                if args.business_date is not None:
                    results = [
                        publish_date(
                            conn,
                            args.dbt_schema,
                            args.published_schema,
                            args.business_date,
                            run_id,
                            code_version(),
                            now,
                            args.note,
                        )
                    ]
                else:
                    results = publish_all(
                        conn,
                        args.dbt_schema,
                        args.published_schema,
                        run_id,
                        code_version(),
                        now,
                        args.note,
                    )
        except PublicationBlockedError as error:
            sys.stderr.write(f"blocked: {error}\n")
            return 2
        outcomes = Counter(r.outcome for r in results)
        out.write(
            f"run {run_id}: {len(results)} business dates; "
            + ", ".join(f"{k.lower()} {v}" for k, v in sorted(outcomes.items()))
            + "\n"
        )
        for r in results:
            if r.outcome != "UNCHANGED":
                out.write(
                    f"  {r.business_date} v{r.snapshot_version} {r.outcome} "
                    f"{r.content_sha256[:12]} {r.restatement_reason or ''}\n"
                )
        return 0
    if args.command == "snapshot-history":
        with settings.connect() as conn:
            for v in snapshot_history(conn, args.published_schema, args.business_date):
                out.write(
                    f"{v.business_date} v{v.snapshot_version} {v.status} "
                    f"published_at={v.published_at} superseded_at={v.superseded_at} "
                    f"sha256={v.content_sha256[:12]} reason={v.restatement_reason}\n"
                )
        return 0

    if args.command == "reconcile":
        run_id = f"recon-{uuid.uuid4().hex[:12]}"
        with settings.connect() as conn:
            outcome = reconcile_warehouse(
                conn,
                args.config,
                args.dbt_schema,
                args.recon_schema,
                run_id,
                code_version(),
                args.as_of,
            )
        out.write(
            f"run {run_id} as of {outcome.as_of}: {outcome.matches} matches "
            f"({outcome.new_matches} new), {outcome.exceptions} exceptions "
            f"({outcome.new_exceptions} new)\n"
        )
        for exception_type, count in outcome.by_type.items():
            out.write(f"  {exception_type} {count}\n")
        lc = outcome.lifecycle
        out.write(
            f"lifecycle: {lc.opened} opened, {lc.source_corrected} source-corrected, "
            f"{lc.auto_resolved} auto-resolved, {lc.escalated} escalated\n"
        )
        return 0
    if args.command == "exception":
        with settings.connect() as conn:
            if args.action == "history":
                for t in history(conn, args.recon_schema, args.exception_id):
                    resolution = f" ({t.resolution_type})" if t.resolution_type else ""
                    out.write(
                        f"{t.sequence} {t.from_status or '-'} -> {t.to_status} by {t.actor} "
                        f"at {t.changed_at.isoformat()}{resolution}: {t.reason}\n"
                    )
                return 0
            try:
                record = transition_exception(
                    conn,
                    args.recon_schema,
                    args.exception_id,
                    Status(args.to),
                    args.actor or "",
                    args.reason or "",
                    Resolution(args.resolution) if args.resolution else None,
                    datetime.now(UTC),
                )
            except IllegalTransitionError as error:
                sys.stderr.write(f"refused: {error}\n")
                return 2
        out.write(f"{record.exception_id}: {record.from_status} -> {record.to_status}\n")
        return 0
    if args.command == "recon-verify":
        with settings.connect() as conn:
            check = verify_against_manifest(conn, args.recon_schema, args.manifest)
        out.write(f"manifest {check.expected} defects, found {check.found} exceptions\n")
        for label, items in (
            ("missing", check.missing),
            ("unexpected", check.unexpected),
            ("impact differs", check.impact_differences),
        ):
            for item in items:
                out.write(f"  {label}: {item}\n")
        out.write("exact match\n" if check.exact else "NOT an exact match\n")
        return 0 if check.exact else 1

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
