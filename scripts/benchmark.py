r"""Load-test benchmark (FR-SIM-06, NFR-08): run the pipeline at load-test size and record it.

Usage (needs ``make up``; takes several minutes)::

    uv run python scripts/benchmark.py --dataset config/simulation/datasets/load.yaml \\
        --write-doc docs/performance/load-test.md

Stages run one after another, each in a fresh measuring process that starts
the stage as its only child and reports the child's wall time and peak
resident memory (``getrusage(RUSAGE_CHILDREN)``):

1. ``simulate``: generate the dataset (events, external records, defects)
2. ``ingest``: validate into the raw layer
3. ``load``: post and load into ``raw_load``/``ledger_load``
4. ``dbt build``: into ``dbt_load``
5. ``publish``: every reporting date into ``published_load``

The four ``*_load`` schemas are dropped first, so each run starts from an empty
warehouse. Raw objects are content-addressed, so a re-run writes no new raw
objects; the results record how many each run wrote. Memory is the Python or
dbt process only: Postgres and SeaweedFS run in Docker and are not included.

Every number in the written table comes from this script's own measurements,
with the command, seed, sizes, code version, machine, and date beside it
(CONTRIBUTING.md W1.5).
"""

import argparse
import json
import os
import platform
import re
import resource
import subprocess
import sys
import time
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from northstar.infrastructure.build_info import code_version

REPO = Path(__file__).resolve().parents[1]
SCHEMAS = ("raw_load", "ledger_load", "dbt_load", "published_load")
START = "<!-- benchmark:start -->"
END = "<!-- benchmark:end -->"
SWEEP_START = "<!-- sweep:start -->"
SWEEP_END = "<!-- sweep:end -->"


@dataclass(frozen=True, slots=True)
class StageResult:
    """One measured stage."""

    stage: str
    command: str
    seconds: float
    peak_rss_mib: float
    exit_code: int
    summary: str


def peak_rss_mib(ru_maxrss: int, system: str) -> float:
    """``ru_maxrss`` in MiB: macOS reports bytes, Linux kibibytes."""
    return ru_maxrss / (1024 * 1024) if system == "Darwin" else ru_maxrss / 1024


def measure(
    command: Sequence[str], cwd: Path, env: dict[str, str]
) -> tuple[float, float, int, str]:
    """Run ``command`` as this process's only child; return seconds, peak MiB, exit code, output."""
    started = time.perf_counter()
    done = subprocess.run(  # noqa: S603 - the commands are fixed by this script
        list(command), cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    seconds = time.perf_counter() - started
    rss = peak_rss_mib(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss, platform.system())
    return seconds, rss, done.returncode, (done.stdout + done.stderr)


def probe(command: Sequence[str], cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    """Run ``command`` under a fresh measuring interpreter, so its peak memory is its own.

    Returns the report: ``seconds``, ``peak_rss_mib``, ``exit_code``, ``output``.
    """
    done = subprocess.run(  # noqa: S603 - re-invokes this script
        [sys.executable, __file__, "_measure", "--cwd", str(cwd), "--", *command],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    report: dict[str, Any] = json.loads(done.stdout.strip().splitlines()[-1])
    return report


def run_stage(stage: str, command: Sequence[str], cwd: Path, env: dict[str, str]) -> StageResult:
    """Measure one pipeline stage."""
    report = probe(command, cwd, env)
    return StageResult(
        stage=stage,
        command=" ".join(command),
        seconds=report["seconds"],
        peak_rss_mib=report["peak_rss_mib"],
        exit_code=report["exit_code"],
        summary=summarize(stage, report["output"]),
    )


_ANSI = re.compile(r"\x1b\[[0-9;]*m")
"""Terminal colour codes (dbt colours its output)."""

_KEEP = {
    "simulate": ("events under", "event trade_executed"),
    "ingest": ("run ",),
    "load": ("run ",),
    "dbt build": ("Done.",),
    "publish": ("run ",),
}


def summarize(stage: str, output: str) -> str:
    """The lines of a stage's output that describe its size and outcome (logs dropped)."""
    keep = _KEEP[stage]
    plain = _ANSI.sub("", output)
    lines = [
        line.strip()
        for line in plain.splitlines()
        if line and not line.lstrip().startswith("{") and any(k in line for k in keep)
    ]
    return " / ".join(lines)[:400] or "(no summary line)"


def render(results: Sequence[StageResult], context: dict[str, str]) -> str:
    """The results as a Markdown section (the part between the doc's markers)."""
    lines = [
        f"### Run of {context['date']}",
        "",
        "| | |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in context.items() if k != "date"],
        "",
        "| Stage | Wall time (s) | Peak memory (MiB) | Result |",
        "|---|---:|---:|---|",
        *[
            f"| {r.stage} | {r.seconds:.1f} | {r.peak_rss_mib:.0f} | "
            f"{'ok' if r.exit_code == 0 else f'exit {r.exit_code}'}: {r.summary} |"
            for r in results
        ],
        "",
        f"Total wall time: {sum(r.seconds for r in results):.1f} s.",
    ]
    return "\n".join(lines)


def write_doc(doc: Path, section: str, start: str = START, end: str = END) -> None:
    """Replace the text between the ``start`` and ``end`` markers in ``doc``."""
    text = doc.read_text(encoding="utf-8")
    if start not in text or end not in text:
        msg = f"{doc} has no {start} / {end} markers"
        raise ValueError(msg)
    head, rest = text.split(start, 1)
    _, tail = rest.split(end, 1)
    doc.write_text(f"{head}{start}\n{section}\n{end}{tail}", encoding="utf-8")


def generate_only(dataset: Path, seed: int, accounts: int) -> str:
    """Generate ``dataset`` with ``accounts`` accounts in memory; return its sizes as JSON."""
    from collections import Counter

    from northstar.application.policies import load_policies
    from northstar.infrastructure.config_loader import load_policy
    from northstar.simulation.generator import DatasetConfig, generate_dataset

    config = load_policy(dataset, DatasetConfig)
    config = config.model_copy(
        update={"accounts": config.accounts.model_copy(update={"count": accounts})}
    )
    events = generate_dataset(config, seed, load_policies(REPO / "config"))
    kinds = Counter(str(e.event_type) for e in events)
    return json.dumps(
        {"accounts": accounts, "events": len(events), "executions": kinds["trade_executed"]}
    )


def render_sweep(rows: Sequence[dict[str, float]], context: dict[str, str]) -> str:
    """Generation-only measurements as a Markdown section."""
    return "\n".join(
        [
            f"### Generation sweep of {context['date']}",
            "",
            "| | |",
            "|---|---|",
            *[f"| {k} | {v} |" for k, v in context.items() if k != "date"],
            "",
            "| Accounts | Events | Executions | Wall time (s) | Peak memory (MiB) |",
            "|---:|---:|---:|---:|---:|",
            *[
                f"| {r['accounts']:,.0f} | {r['events']:,.0f} | {r['executions']:,.0f} | "
                f"{r['seconds']:.1f} | {r['peak_rss_mib']:.0f} |"
                for r in rows
            ],
        ]
    )


def _drop_schemas(env: dict[str, str]) -> None:
    code = (
        "from northstar.infrastructure.postgres import PostgresSettings\n"
        "with PostgresSettings.from_env().connect() as c:\n"
        f"    for s in {SCHEMAS!r}:\n"
        "        c.execute(f'drop schema if exists {s} cascade')\n"
        "    c.commit()\n"
    )
    subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, check=True)  # noqa: S603


def main(argv: Sequence[str]) -> int:
    """Run the benchmark, print the results, and optionally write them into a doc."""
    if argv and argv[0] == "_generate":
        sys.stdout.write(generate_only(Path(argv[1]), int(argv[2]), int(argv[3])) + "\n")
        return 0
    if argv and argv[0] == "_measure":
        cwd = Path(argv[argv.index("--cwd") + 1])
        command = argv[argv.index("--") + 1 :]
        seconds, rss, code, output = measure(command, cwd, dict(os.environ))
        report = {"seconds": seconds, "peak_rss_mib": rss, "exit_code": code, "output": output}
        sys.stdout.write(json.dumps(report) + "\n")
        return 0
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--defects", type=Path, default=Path("config/simulation/defects/dev.yaml"))
    parser.add_argument("--write-doc", type=Path, help="Markdown file with benchmark markers")
    parser.add_argument(
        "--sweep",
        type=int,
        nargs="+",
        metavar="ACCOUNTS",
        help="measure generation only, once per account count (no stack needed)",
    )
    args = parser.parse_args(argv)
    if args.sweep:
        return _sweep(args)

    env = dict(os.environ)
    northstar = [str(Path(sys.executable).parent / "northstar")]
    dataset_id = args.dataset.stem
    root = Path("data/generated") / dataset_id / f"seed={args.seed}"
    run_id = f"bench-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    dbt_vars = json.dumps({"raw_schema": "raw_load", "ledger_schema": "ledger_load"})
    _drop_schemas(env)
    stages = [
        (
            "simulate",
            [
                *northstar,
                "simulate",
                "--dataset",
                str(args.dataset),
                "--seed",
                str(args.seed),
                "--out",
                "data/generated",
                "--defects",
                str(args.defects),
            ],
            REPO,
        ),
        ("ingest", [*northstar, "ingest", "--dataset-dir", str(root), "--run-id", run_id], REPO),
        (
            "load",
            [
                *northstar,
                "load-run",
                "--run-id",
                run_id,
                "--raw-schema",
                "raw_load",
                "--ledger-schema",
                "ledger_load",
            ],
            REPO,
        ),
        (
            "dbt build",
            [str(Path(sys.executable).parent / "dbt"), "build", "--vars", dbt_vars],
            REPO / "warehouse" / "dbt",
        ),
        (
            "publish",
            [
                *northstar,
                "publish",
                "--dbt-schema",
                "dbt_load",
                "--published-schema",
                "published_load",
            ],
            REPO,
        ),
    ]
    results = []
    for stage, command, cwd in stages:
        stage_env = env | ({"NBI_DBT_SCHEMA": "dbt_load"} if stage == "dbt build" else {})
        result = run_stage(stage, command, cwd, stage_env)
        results.append(result)
        sys.stderr.write(
            f"{stage}: {result.seconds:.1f} s, {result.peak_rss_mib:.0f} MiB, "
            f"exit {result.exit_code}\n"
        )
        if result.exit_code != 0:
            break
    context = _context(args, f"uv run python scripts/benchmark.py --dataset {args.dataset}")
    context["Dataset, seed"] += f", defects `{args.defects}`"
    section = render(results, context)
    sys.stdout.write(section + "\n")
    for r in results:
        sys.stderr.write(json.dumps(asdict(r)) + "\n")
    if args.write_doc is not None and all(r.exit_code == 0 for r in results):
        write_doc(args.write_doc, section)
    return 0 if all(r.exit_code == 0 for r in results) and len(results) == len(stages) else 1


def _context(args: argparse.Namespace, command: str) -> dict[str, str]:
    return {
        "date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "Command": f"`{command}`",
        "Dataset, seed": f"`{args.dataset}`, seed {args.seed}",
        "Code version": code_version(repo=REPO),
        "Machine": (
            f"{platform.machine()}, {os.cpu_count()} CPUs, {platform.system()} {platform.release()}"
        ),
        "Python": platform.python_version(),
    }


def _sweep(args: argparse.Namespace) -> int:
    rows = []
    for accounts in args.sweep:
        report = probe(
            [
                sys.executable,
                __file__,
                "_generate",
                str(args.dataset),
                str(args.seed),
                str(accounts),
            ],
            REPO,
            dict(os.environ),
        )
        if report["exit_code"] != 0:
            sys.stderr.write(f"{accounts} accounts failed:\n{report['output'][-2000:]}\n")
            return 1
        sizes = json.loads(report["output"].strip().splitlines()[-1])
        rows.append(sizes | {"seconds": report["seconds"], "peak_rss_mib": report["peak_rss_mib"]})
        sys.stderr.write(
            f"{accounts}: {report['seconds']:.1f} s, {report['peak_rss_mib']:.0f} MiB\n"
        )
    command = f"uv run python scripts/benchmark.py --dataset {args.dataset} --sweep " + " ".join(
        str(n) for n in args.sweep
    )
    section = render_sweep(rows, _context(args, command))
    sys.stdout.write(section + "\n")
    if args.write_doc is not None:
        write_doc(args.write_doc, section, SWEEP_START, SWEEP_END)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
