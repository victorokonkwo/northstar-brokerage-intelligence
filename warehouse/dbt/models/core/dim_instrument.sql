-- Instruments in SCD Type 2 shape. Today each instrument has exactly one
-- version (its listing): no event changes an instrument yet. Option terms are
-- null for equities.
with listed as (
    select
        instrument_id,
        symbol,
        instrument_name,
        market,
        asset_class,
        currency,
        multiplier,
        underlying_instrument_id,
        strike,
        expiry,
        option_right,
        exercise_style,
        event_timestamp as valid_from,
        event_id,
        schema_version,
        _ingestion_run_id,
        _ingested_at
    from {{ ref('stg_brokerage__instrument_listed') }}
)

select
    {{ surrogate_key(['instrument_id', 'valid_from']) }} as instrument_sk,
    instrument_id,
    symbol,
    instrument_name,
    market,
    asset_class,
    currency,
    multiplier,
    underlying_instrument_id,
    strike,
    expiry,
    option_right,
    exercise_style,
    valid_from,
    lead(valid_from) over (partition by instrument_id order by valid_from) as valid_to,
    lead(valid_from) over (partition by instrument_id order by valid_from) is null as is_current,
    event_id as _source_event_id,
    _ingestion_run_id,
    _ingested_at,
    schema_version as _schema_version,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from listed
