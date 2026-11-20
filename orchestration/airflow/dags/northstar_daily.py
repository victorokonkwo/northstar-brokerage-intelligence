"""Daily close for the dev dataset (FR-M1-07): ingest, load, dbt build, publish.

Each run processes business date D = the day before its logical date. Runs
trigger at 06:00 America/Toronto, after D has ended (domain model §5).
Every task calls the ``northstar`` CLI in the image's own virtualenv; the DAG
holds no business logic (system overview §4).

- ``check_business_day`` skips the whole run when D is not a ledger business
  day (the CLI exits 99).
- ``ingest`` validates the dataset into the raw layer. Its run ID carries the
  try number, because each ingestion run writes a manifest exactly once.
- ``load`` loads that ingestion run into the warehouse.
- ``dbt_build`` runs ``dbt-ol build`` (dbt plus OpenLineage events in a file)
  and stores the events under ``audit/lineage/dbt-<run>.jsonl``.
- ``publish`` publishes D as a versioned snapshot (ADR 006).

Retries come from ``NBI_AIRFLOW_RETRIES`` and ``NBI_AIRFLOW_RETRY_DELAY_SECONDS``.
When a task fails after its last retry, ``alert`` runs ``northstar
record-alert``, which logs it and stores it under ``audit/alerts/``. A
backfill uses the same tasks, so N backfilled dates equal N daily runs.

Tasks that write the warehouse (load, dbt_build, publish) share the one-slot
``warehouse`` pool, so concurrent runs, such as a backfill next to a triggered
run, take turns instead of deadlocking.

Runs are limited to the dataset's window (start/end dates below): the dev
dataset is synthetic Q1 2026 data, so later dates have nothing to publish.
"""

import logging
import os
import subprocess
from datetime import timedelta
from typing import Any

import pendulum
from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG
from airflow.timetables.trigger import CronTriggerTimetable

VENV_BIN = "/opt/northstar/.venv/bin"
NORTHSTAR = f"{VENV_BIN}/northstar"
DBT_OL = f"{VENV_BIN}/dbt-ol"
PROJECT = "/opt/northstar"
TORONTO = pendulum.timezone("America/Toronto")
NOT_A_BUSINESS_DAY = 99
WAREHOUSE_POOL = "warehouse"
"""One slot (created by airflow-init): tasks that write the warehouse never overlap.
max_active_runs alone doesn't stop a backfill from running beside a triggered
run, and two concurrent dbt builds on one schema deadlock in Postgres."""

BUSINESS_DATE = "{{ macros.ds_add(ds, -1) }}"
RUN = "{{ run_id | replace(':', '-') | replace('+', '-') }}-try{{ ti.try_number }}"
DATASET_VARS = (
    '\'{"raw_schema": "{{ params.raw_schema }}", "ledger_schema": "{{ params.ledger_schema }}"}\''
)


def alert(context: dict[str, Any]) -> None:
    """Record a task that failed after its retries (it never raises: alerting must not mask)."""
    ti = context["ti"]
    message = str(context.get("exception") or "task failed")[:2000]
    command = [
        NORTHSTAR,
        "record-alert",
        "--dag-id",
        ti.dag_id,
        "--task-id",
        ti.task_id,
        "--run-id",
        ti.run_id,
        "--try-number",
        str(ti.try_number),
        "--message",
        message,
    ]
    result = subprocess.run(  # noqa: S603 - fixed executable and arguments, no shell
        command, cwd=PROJECT, capture_output=True, text=True, timeout=120, check=False
    )
    log = logging.getLogger("northstar.alerts")
    if result.returncode == 0:
        log.error("ALERT %s.%s run %s: %s", ti.dag_id, ti.task_id, ti.run_id, message)
    else:
        log.error(
            "ALERT %s.%s run %s (could not be stored: %s): %s",
            ti.dag_id,
            ti.task_id,
            ti.run_id,
            result.stderr.strip()[-500:],
            message,
        )


with DAG(
    dag_id="northstar_daily",
    description="Daily close: ingest, load, dbt build with lineage, publish snapshot",
    schedule=CronTriggerTimetable("0 6 * * *", timezone=TORONTO),
    start_date=pendulum.datetime(2026, 1, 3, tz=TORONTO),
    end_date=pendulum.datetime(2026, 4, 2, 23, 59, tz=TORONTO),
    catchup=False,
    max_active_runs=1,
    params={
        "dataset_dir": "/opt/northstar/data/generated/dev/seed=42",
        "raw_schema": "raw_dev",
        "ledger_schema": "ledger_dev",
        "dbt_schema": "dbt_dataset",
        "published_schema": "published_dev",
    },
    default_args={
        "retries": int(os.environ.get("NBI_AIRFLOW_RETRIES", "2")),
        "retry_delay": timedelta(
            seconds=int(os.environ.get("NBI_AIRFLOW_RETRY_DELAY_SECONDS", "300"))
        ),
        "on_failure_callback": alert,
        "cwd": PROJECT,
        "append_env": True,
    },
    tags=["northstar", "m1"],
) as dag:
    check_business_day = BashOperator(
        task_id="check_business_day",
        bash_command=f"{NORTHSTAR} is-business-day --date {BUSINESS_DATE}",
        skip_on_exit_code=NOT_A_BUSINESS_DAY,
        retries=0,
    )
    ingest = BashOperator(
        task_id="ingest",
        bash_command=(
            f'{NORTHSTAR} ingest --dataset-dir "{{{{ params.dataset_dir }}}}" '
            f'--run-id "ingest-{RUN}" >&2 && echo "ingest-{RUN}"'
        ),
    )
    load = BashOperator(
        task_id="load",
        pool=WAREHOUSE_POOL,
        bash_command=(
            f"{NORTHSTAR} load-run --run-id \"{{{{ ti.xcom_pull(task_ids='ingest') }}}}\" "
            "--raw-schema {{ params.raw_schema }} --ledger-schema {{ params.ledger_schema }}"
        ),
    )
    # dbt-ol writes OpenLineage events to a file for this try; the events are
    # archived even when dbt fails (FAIL events are lineage too), and the task
    # then exits with dbt's status, or 1 if only the archiving failed.
    dbt_build = BashOperator(
        task_id="dbt_build",
        pool=WAREHOUSE_POOL,
        bash_command=(
            f"mkdir -p /tmp/lineage && rm -f /tmp/lineage/{RUN}.jsonl && "
            f"printf 'transport:\\n  type: file\\n  log_file_path: /tmp/lineage/{RUN}.jsonl\\n"
            f"  append: true\\n' > /tmp/lineage/{RUN}.yml && "
            f"(cd {PROJECT}/warehouse/dbt && "
            # dbt-ol runs `dbt` from PATH: the project's dbt, not anything else.
            f"PATH={VENV_BIN}:$PATH "
            f"OPENLINEAGE_CONFIG=/tmp/lineage/{RUN}.yml OPENLINEAGE_NAMESPACE=northstar "
            f"NBI_DBT_SCHEMA={{{{ params.dbt_schema }}}} {DBT_OL} build --vars {DATASET_VARS}); "
            "status=$?; "
            f'{NORTHSTAR} archive-lineage --file /tmp/lineage/{RUN}.jsonl --run-id "dbt-{RUN}" '
            "|| { [ $status -eq 0 ] && status=1; }; exit $status"
        ),
    )
    publish = BashOperator(
        task_id="publish",
        pool=WAREHOUSE_POOL,
        bash_command=(
            f"{NORTHSTAR} publish --business-date {BUSINESS_DATE} "
            "--dbt-schema {{ params.dbt_schema }} --published-schema {{ params.published_schema }}"
        ),
    )

    check_business_day >> ingest >> load >> dbt_build >> publish
