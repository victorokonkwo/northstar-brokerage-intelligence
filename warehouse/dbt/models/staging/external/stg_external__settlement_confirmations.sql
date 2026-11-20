-- One row per settlement confirmation (the depository's view of an obligation). Rename and cast
-- only.
with source as (
    select * from {{ source('external', 'external_records') }}
    where record_set = 'settlement_confirmations'
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
    (payload ->> 'amount')::numeric(18, 2) as amount,
    payload ->> 'currency' as currency,
    (payload ->> 'contractual_settlement_date')::date as contractual_settlement_date,
    (payload ->> 'actual_settlement_date')::date as actual_settlement_date
from source
