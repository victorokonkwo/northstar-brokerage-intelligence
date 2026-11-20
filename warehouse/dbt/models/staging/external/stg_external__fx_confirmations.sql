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
    payload ->> 'broker_reference' as broker_reference,
    payload ->> 'account_id' as account_id,
    (payload ->> 'sell_amount')::numeric(18, 2) as sell_amount,
    payload ->> 'sell_currency' as sell_currency,
    (payload ->> 'buy_amount')::numeric(18, 2) as buy_amount,
    payload ->> 'buy_currency' as buy_currency,
    (payload ->> 'client_rate')::numeric(18, 10) as client_rate,
    (payload ->> 'trade_date')::date as trade_date
from source
