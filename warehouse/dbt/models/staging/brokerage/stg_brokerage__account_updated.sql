-- One row per account_updated event (the account's full new state). Rename and cast only.
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'account_updated'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'account_id') }} as account_id,
    {{ json_text('payload', 'account_type') }} as account_type,
    {{ json_text('payload', 'status') }} as status,
    {{ json_text('payload', 'reason') }} as reason
from source
