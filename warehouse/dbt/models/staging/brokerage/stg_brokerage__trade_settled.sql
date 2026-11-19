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
    payload ->> 'settlement_id' as settlement_id,
    payload ->> 'order_id' as order_id,
    payload ->> 'account_id' as account_id,
    payload ->> 'instrument_id' as instrument_id,
    payload ->> 'side' as side,
    (payload ->> 'trade_date')::date as trade_date,
    (payload ->> 'settlement_date')::date as settlement_date,
    (payload ->> 'quantity')::numeric(18, 8) as quantity,
    (payload ->> 'amount')::numeric(18, 2) as amount,
    payload ->> 'currency' as currency,
    payload -> 'execution_ids' as execution_ids
from source
