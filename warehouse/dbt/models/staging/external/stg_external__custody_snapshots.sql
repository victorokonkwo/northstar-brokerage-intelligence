-- One row per custody snapshot line (settled holdings per account, instrument, and date). Rename
-- and cast only.
with source as (
    select * from {{ source('external', 'external_records') }}
    where record_set = 'custody_snapshots'
)

select
    record_id,
    event_date,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    (payload ->> 'snapshot_date')::date as snapshot_date,
    payload ->> 'account_id' as account_id,
    payload ->> 'instrument_id' as instrument_id,
    (payload ->> 'settled_quantity')::numeric(18, 8) as settled_quantity
from source
