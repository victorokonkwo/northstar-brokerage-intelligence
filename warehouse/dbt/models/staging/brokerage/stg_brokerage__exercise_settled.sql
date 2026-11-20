-- One row per exercise_settled event (roadmap 5C-b): an exercise's underlying
-- delivered against its strike amount. side is the client's. Rename and cast only.
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'exercise_settled'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'settlement_id') }} as settlement_id,
    {{ json_text('payload', 'exercise_id') }} as exercise_id,
    {{ json_text('payload', 'account_id') }} as account_id,
    {{ json_text('payload', 'instrument_id') }} as instrument_id,
    {{ json_text('payload', 'side') }} as side,
    ({{ json_text('payload', 'quantity') }})::numeric(18, 8) as quantity,
    ({{ json_text('payload', 'amount') }})::numeric(18, 2) as amount,
    {{ json_text('payload', 'currency') }} as currency,
    ({{ json_text('payload', 'settlement_date') }})::date as settlement_date
from source
