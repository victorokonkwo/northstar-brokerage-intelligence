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
    {{ json_text('payload', 'broker_reference') }} as broker_reference,
    {{ json_text('payload', 'account_id') }} as account_id,
    {{ json_text('payload', 'instrument_id') }} as instrument_id,
    {{ json_text('payload', 'side') }} as side,
    ({{ json_text('payload', 'quantity') }})::numeric(18, 8) as quantity,
    ({{ json_text('payload', 'price') }})::numeric(18, 8) as price,
    {{ json_text('payload', 'currency') }} as currency,
    ({{ json_text('payload', 'trade_date') }})::date as trade_date
from source
