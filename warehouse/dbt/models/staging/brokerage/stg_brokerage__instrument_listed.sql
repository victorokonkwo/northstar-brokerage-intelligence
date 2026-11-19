-- One row per instrument_listed event. Option terms are null for equities. Rename and cast only.
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'instrument_listed'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    payload ->> 'instrument_id' as instrument_id,
    payload ->> 'symbol' as symbol,
    payload ->> 'name' as instrument_name,
    payload ->> 'market' as market,
    payload ->> 'asset_class' as asset_class,
    payload ->> 'currency' as currency,
    (payload ->> 'multiplier')::integer as multiplier,
    payload -> 'option' ->> 'underlying_instrument_id' as underlying_instrument_id,
    (payload -> 'option' ->> 'strike')::numeric(18, 8) as strike,
    (payload -> 'option' ->> 'expiry')::date as expiry,
    payload -> 'option' ->> 'right' as option_right,
    payload -> 'option' ->> 'exercise_style' as exercise_style
from source
