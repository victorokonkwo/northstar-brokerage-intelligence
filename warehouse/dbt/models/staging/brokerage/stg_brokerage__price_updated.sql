-- One row per price_updated event (a daily close). Rename and cast only.
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'price_updated'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'instrument_id') }} as instrument_id,
    ({{ json_text('payload', 'price_date') }})::date as price_date,
    ({{ json_text('payload', 'close_price') }})::numeric(18, 8) as close_price,
    {{ json_text('payload', 'currency') }} as currency,
    {{ json_text('payload', 'source') }} as price_source
from source
