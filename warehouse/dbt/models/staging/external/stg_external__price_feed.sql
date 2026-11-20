-- One row per vendor closing price. Rename and cast only.
with source as (
    select * from {{ source('external', 'external_records') }}
    where record_set = 'price_feed'
)

select
    record_id,
    event_date,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    payload ->> 'instrument_id' as instrument_id,
    (payload ->> 'price_date')::date as price_date,
    (payload ->> 'close_price')::numeric(18, 8) as close_price,
    payload ->> 'currency' as currency
from source
