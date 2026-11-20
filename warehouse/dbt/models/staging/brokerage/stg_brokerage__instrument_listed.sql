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
    {{ json_text('payload', 'instrument_id') }} as instrument_id,
    {{ json_text('payload', 'symbol') }} as symbol,
    {{ json_text('payload', 'name') }} as instrument_name,
    {{ json_text('payload', 'market') }} as market,
    {{ json_text('payload', 'asset_class') }} as asset_class,
    {{ json_text('payload', 'currency') }} as currency,
    ({{ json_text('payload', 'multiplier') }})::integer as multiplier,
    {{ json_text('payload', 'option', 'underlying_instrument_id') }} as underlying_instrument_id,
    ({{ json_text('payload', 'option', 'strike') }})::numeric(18, 8) as strike,
    ({{ json_text('payload', 'option', 'expiry') }})::date as expiry,
    {{ json_text('payload', 'option', 'right') }} as option_right,
    {{ json_text('payload', 'option', 'exercise_style') }} as exercise_style
from source
