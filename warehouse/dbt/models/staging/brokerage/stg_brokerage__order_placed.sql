-- One row per order_placed event. Rename and cast only.
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'order_placed'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    payload ->> 'order_id' as order_id,
    payload ->> 'account_id' as account_id,
    payload ->> 'instrument_id' as instrument_id,
    payload ->> 'market' as market,
    payload ->> 'side' as side,
    payload ->> 'order_type' as order_type,
    (payload ->> 'quantity')::numeric(18, 8) as quantity,
    (payload ->> 'limit_price')::numeric(18, 8) as limit_price,
    payload ->> 'currency' as currency
from source
