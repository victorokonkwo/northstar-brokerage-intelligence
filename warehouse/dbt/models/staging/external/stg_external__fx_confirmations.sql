-- One row per FX confirmation (the FX desk's view of a client conversion). Rates are CAD per 1
-- USD. Rename and cast only.
with source as (
    select * from {{ source('external', 'external_records') }}
    where record_set = 'fx_confirmations'
)

select
    record_id,
    event_date,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'broker_reference') }} as broker_reference,
    {{ json_text('payload', 'account_id') }} as account_id,
    ({{ json_text('payload', 'sell_amount') }})::numeric(18, 2) as sell_amount,
    {{ json_text('payload', 'sell_currency') }} as sell_currency,
    ({{ json_text('payload', 'buy_amount') }})::numeric(18, 2) as buy_amount,
    {{ json_text('payload', 'buy_currency') }} as buy_currency,
    ({{ json_text('payload', 'client_rate') }})::numeric(18, 10) as client_rate,
    ({{ json_text('payload', 'trade_date') }})::date as trade_date
from source
