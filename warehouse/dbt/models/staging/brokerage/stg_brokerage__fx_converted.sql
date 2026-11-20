-- One row per fx_converted event. Rates are CAD per 1 USD. Rename and cast only.
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'fx_converted'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'fx_conversion_id') }} as fx_conversion_id,
    {{ json_text('payload', 'account_id') }} as account_id,
    ({{ json_text('payload', 'sell_amount') }})::numeric(18, 2) as sell_amount,
    {{ json_text('payload', 'sell_currency') }} as sell_currency,
    ({{ json_text('payload', 'buy_amount') }})::numeric(18, 2) as buy_amount,
    {{ json_text('payload', 'buy_currency') }} as buy_currency,
    ({{ json_text('payload', 'reference_rate') }})::numeric(18, 10) as reference_rate,
    ({{ json_text('payload', 'client_rate') }})::numeric(18, 10) as client_rate,
    ({{ json_text('payload', 'rate_date') }})::date as rate_date,
    {{ json_text('payload', 'rate_source') }} as rate_source,
    {{ json_text('payload', 'fx_policy_version') }} as fx_policy_version
from source
