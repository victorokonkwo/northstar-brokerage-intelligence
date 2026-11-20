-- One row per trade_settled event (one settlement obligation). Rename and cast only.
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'trade_settled'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'settlement_id') }} as settlement_id,
    {{ json_text('payload', 'order_id') }} as order_id,
    {{ json_text('payload', 'account_id') }} as account_id,
    {{ json_text('payload', 'instrument_id') }} as instrument_id,
    {{ json_text('payload', 'side') }} as side,
    ({{ json_text('payload', 'trade_date') }})::date as trade_date,
    ({{ json_text('payload', 'settlement_date') }})::date as settlement_date,
    ({{ json_text('payload', 'quantity') }})::numeric(18, 8) as quantity,
    ({{ json_text('payload', 'amount') }})::numeric(18, 2) as amount,
    {{ json_text('payload', 'currency') }} as currency,
    {{ json_value('payload', 'execution_ids') }} as execution_ids
from source
