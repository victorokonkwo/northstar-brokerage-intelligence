#!/bin/sh
# One step of the cloud daily close (roadmap 4G), run as a Fargate task by the
# Step Functions state machine. It mirrors the Airflow DAG's tasks
# (orchestration/airflow/dags/northstar_daily.py) and holds no business logic.
#
#   run_step.sh check_business_day|ingest_and_load|dbt_build|reconcile|
#               dbt_controls|publish_controls|publish
#
# The state machine sets NBI_BUSINESS_DATE (the date being closed) and NBI_RUN
# (unique per execution and attempt). The task definition sets the warehouse
# connection and the schemas (NBI_*_SCHEMA). Exit codes are the CLI's: 99 from
# check_business_day means "not a business day", and the state machine stops
# without failing.
set -eu

: "${NBI_BUSINESS_DATE:?}" "${NBI_RUN:?}"

dbt_ol() {
  # dbt with OpenLineage events in a file, archived even when dbt fails (as the
  # DAG's dbt_ol helper does); exits with dbt's status, or 1 if only archiving failed.
  suffix=$1
  shift
  events=/tmp/lineage/$NBI_RUN-$suffix.jsonl
  config=/tmp/lineage/$NBI_RUN-$suffix.yml
  mkdir -p /tmp/lineage
  printf 'transport:\n  type: file\n  log_file_path: %s\n  append: true\n' "$events" > "$config"
  status=0
  (cd /opt/northstar/warehouse/dbt && OPENLINEAGE_CONFIG=$config OPENLINEAGE_NAMESPACE=northstar \
    dbt-ol "$@" --vars "{\"raw_schema\": \"$NBI_RAW_SCHEMA\", \"ledger_schema\": \"$NBI_LEDGER_SCHEMA\", \"recon_schema\": \"$NBI_RECON_SCHEMA\"}") \
    || status=$?
  northstar archive-lineage --file "$events" --run-id "dbt-$suffix-$NBI_RUN" \
    || { [ "$status" -eq 0 ] && status=1; }
  return "$status"
}

case ${1:-} in
  check_business_day)
    northstar is-business-day --date "$NBI_BUSINESS_DATE"
    ;;
  ingest_and_load)
    # One task, because the load needs the ingestion run's ID. The dev dataset
    # is generated, deterministically, inside the task; re-ingesting it adds
    # nothing that is already in the bronze bucket.
    northstar simulate --dataset config/simulation/datasets/dev.yaml --seed 42 \
      --defects config/simulation/defects/dev.yaml --out /tmp/data >&2
    northstar ingest --dataset-dir /tmp/data/dev/seed=42 --run-id "$NBI_RUN"
    northstar load-run --run-id "$NBI_RUN" --raw-schema "$NBI_RAW_SCHEMA" \
      --ledger-schema "$NBI_LEDGER_SCHEMA" --recon-schema "$NBI_RECON_SCHEMA"
    ;;
  dbt_build)
    dbt_ol core build
    ;;
  reconcile)
    northstar reconcile --dbt-schema "$NBI_DBT_SCHEMA" --recon-schema "$NBI_RECON_SCHEMA" \
      --as-of "$NBI_BUSINESS_DATE"
    ;;
  dbt_controls)
    dbt_ol controls build --select tag:controls
    ;;
  publish_controls)
    northstar publish --stream controls --business-date "$NBI_BUSINESS_DATE" \
      --dbt-schema "$NBI_DBT_SCHEMA" --published-schema "$NBI_PUBLISHED_CONTROLS_SCHEMA"
    ;;
  publish)
    northstar publish --stream financial --business-date "$NBI_BUSINESS_DATE" \
      --dbt-schema "$NBI_DBT_SCHEMA" --published-schema "$NBI_PUBLISHED_SCHEMA" \
      --recon-schema "$NBI_RECON_SCHEMA"
    ;;
  *)
    echo "usage: $0 check_business_day|ingest_and_load|dbt_build|reconcile|dbt_controls|publish_controls|publish" >&2
    exit 2
    ;;
esac
