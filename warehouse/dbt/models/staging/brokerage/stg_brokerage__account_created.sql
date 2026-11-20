-- One row per account_created event. Rename and cast only.
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'account_created'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'account_id') }} as account_id,
    {{ json_text('payload', 'account_type') }} as account_type,
    {{ json_text('payload', 'base_currency') }} as base_currency
from source
