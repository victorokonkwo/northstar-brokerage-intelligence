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
    {{ json_text('payload', 'order_id') }} as order_id,
    {{ json_text('payload', 'account_id') }} as account_id,
    {{ json_text('payload', 'instrument_id') }} as instrument_id,
    {{ json_text('payload', 'market') }} as market,
    {{ json_text('payload', 'side') }} as side,
    {{ json_text('payload', 'order_type') }} as order_type,
    ({{ json_text('payload', 'quantity') }})::numeric(18, 8) as quantity,
    ({{ json_text('payload', 'limit_price') }})::numeric(18, 8) as limit_price,
    {{ json_text('payload', 'currency') }} as currency
from source
