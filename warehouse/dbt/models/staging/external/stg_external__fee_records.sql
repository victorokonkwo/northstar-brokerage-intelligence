-- One row per fee record (the clearing firm's fee for an execution). Rename and cast only.
with source as (
    select * from {{ source('external', 'external_records') }}
    where record_set = 'fee_records'
)

select
    record_id,
    event_date,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    payload ->> 'broker_reference' as broker_reference,
    payload ->> 'account_id' as account_id,
    (payload ->> 'trade_date')::date as trade_date,
    payload ->> 'currency' as currency,
    (payload ->> 'amount')::numeric(18, 2) as amount
from source
