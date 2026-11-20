#!/usr/bin/env bash
# The 4F-c parity run: the same pipeline on one warehouse, from nothing.
#
#   scripts/parity.sh postgres|redshift   run it on that warehouse
#   scripts/parity.sh compare             compare the two runs, table for table
#
# The sequence is the seed-42 dev dataset (make simulate), then the late
# deposit: ingest, load, dbt build, reconcile, controls marts, publish both
# streams. No exception is changed by hand, so both warehouses see exactly the
# same inputs. Every dbt model, reconciliation table, and published table must
# then be identical on both, apart from the provenance columns listed below.
#
# Run from the repository root with .env loaded. Redshift also needs
# `eval "$(aws configure export-credentials --format env)"` and
# `eval "$(uv run python scripts/redshift_env.py)"` (make parity does this).
set -euo pipefail

DATASET=data/generated/dev/seed=42
LATE=tests/fixtures/late_deposit
VARS='{"raw_schema": "raw_parity", "ledger_schema": "ledger_parity", "recon_schema": "recon_parity"}'
# When a run happened, and which run: they differ between any two runs.
PROVENANCE=(run_id _detected_run_id created_at changed_at opened_at resolved_at published_at
            superseded_at code_version)

run() {
  local warehouse=$1 target=local stamp
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  [[ $warehouse == redshift ]] && target=redshift
  nbi() { uv run northstar --warehouse "$warehouse" "$@"; }
  dbt_build() {
    (cd warehouse/dbt && NBI_DBT_TARGET=$target NBI_DBT_SCHEMA=dbt_parity \
      uv run dbt build --vars "$VARS" "$@")
  }
  publish() {
    # Exit 2 means some dates stay DRAFT behind open blocking exceptions: expected.
    nbi publish --stream controls --dbt-schema dbt_parity \
      --published-schema published_controls_parity
    nbi publish --stream financial --dbt-schema dbt_parity --published-schema published_parity \
      --recon-schema recon_parity || [[ $? -eq 2 ]]
  }
  bootstrap() {
    [[ $warehouse == redshift ]] || return 0
    nbi redshift-bootstrap --raw-schema raw_parity --ledger-schema ledger_parity \
      --recon-schema recon_parity "$@"
  }

  uv run python scripts/parity_reset.py --warehouse "$warehouse"
  bootstrap
  for step in base late; do
    local dir=$DATASET run_id=parity-$warehouse-$step-$stamp
    [[ $step == late ]] && dir=$LATE
    nbi ingest --dataset-dir "$dir" --run-id "$run_id"
    nbi load-run --run-id "$run_id" --raw-schema raw_parity --ledger-schema ledger_parity \
      --recon-schema recon_parity
    dbt_build
    # The published copies take the dbt marts' columns, so they follow the first build.
    [[ $step == base ]] && bootstrap --dbt-schema dbt_parity \
      --published-schema published_parity --published-controls-schema published_controls_parity
    nbi reconcile --dbt-schema dbt_parity --recon-schema recon_parity
    dbt_build --select tag:controls
    publish
  done
}

compare() {
  local ignore=() schema status=0
  for column in "${PROVENANCE[@]}"; do ignore+=(--ignore "$column"); done
  for schema in dbt_parity recon_parity published_parity published_controls_parity; do
    uv run python scripts/compare_warehouses.py --schema "$schema" "${ignore[@]}" || status=1
  done
  return $status
}

case ${1:-} in
  postgres | redshift) run "$1" ;;
  compare) compare ;;
  *) echo "usage: $0 postgres|redshift|compare" >&2; exit 2 ;;
esac
