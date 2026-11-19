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
    payload ->> 'instrument_id' as instrument_id,
    (payload ->> 'price_date')::date as price_date,
    (payload ->> 'close_price')::numeric(18, 8) as close_price,
    payload ->> 'currency' as currency,
    payload ->> 'source' as price_source
from source
