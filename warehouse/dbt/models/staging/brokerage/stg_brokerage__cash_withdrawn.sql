-- One row per cash_withdrawn event. Rename and cast only.
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'cash_withdrawn'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'cash_movement_id') }} as cash_movement_id,
    {{ json_text('payload', 'account_id') }} as account_id,
    ({{ json_text('payload', 'amount') }})::numeric(18, 2) as amount,
    {{ json_text('payload', 'currency') }} as currency
from source
