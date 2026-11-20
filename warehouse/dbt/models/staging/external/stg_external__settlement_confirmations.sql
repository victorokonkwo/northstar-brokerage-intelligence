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
    {{ json_text('payload', 'broker_reference') }} as broker_reference,
    {{ json_text('payload', 'account_id') }} as account_id,
    {{ json_text('payload', 'instrument_id') }} as instrument_id,
    {{ json_text('payload', 'side') }} as side,
    ({{ json_text('payload', 'quantity') }})::numeric(18, 8) as quantity,
    ({{ json_text('payload', 'amount') }})::numeric(18, 2) as amount,
    {{ json_text('payload', 'currency') }} as currency,
    (
        {{ json_text('payload', 'contractual_settlement_date') }}
    )::date as contractual_settlement_date,
    ({{ json_text('payload', 'actual_settlement_date') }})::date as actual_settlement_date
from source
