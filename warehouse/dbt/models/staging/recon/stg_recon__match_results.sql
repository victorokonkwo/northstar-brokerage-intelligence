-- One row per recon.match_results row. Rename and cast only.
with source as (
    select * from {{ source('recon', 'match_results') }}
)

select
    match_id,
    reconciliation,
    rule_id,
    rule_version,
    internal_ref,
    external_record_ids,
    business_date,
    run_id,
    _ingested_at
from source
