-- One row per clearing confirmation (the clearing firm's view of an execution). Rename and cast
-- only.
with source as (
    select * from {{ source('external', 'external_records') }}
    where record_set = 'clearing_confirmations'
)

select
    record_id,
    event_date,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    payload ->> 'broker_reference' as broker_reference,
    payload ->> 'account_id' as account_id,
    payload ->> 'instrument_id' as instrument_id,
    payload ->> 'side' as side,
    (payload ->> 'quantity')::numeric(18, 8) as quantity,
    (payload ->> 'price')::numeric(18, 8) as price,
    payload ->> 'currency' as currency,
    (payload ->> 'trade_date')::date as trade_date
from source
