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
    {{ json_text('payload', 'broker_reference') }} as broker_reference,
    {{ json_text('payload', 'account_id') }} as account_id,
    ({{ json_text('payload', 'trade_date') }})::date as trade_date,
    {{ json_text('payload', 'currency') }} as currency,
    ({{ json_text('payload', 'amount') }})::numeric(18, 2) as amount
from source
