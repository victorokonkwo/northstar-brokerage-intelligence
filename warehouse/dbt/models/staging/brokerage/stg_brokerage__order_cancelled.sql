-- One row per order_cancelled event (the unfilled remainder). Rename and cast only.
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'order_cancelled'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'order_id') }} as order_id,
    {{ json_text('payload', 'account_id') }} as account_id,
    ({{ json_text('payload', 'cancelled_quantity') }})::numeric(18, 8) as cancelled_quantity
from source
