-- One row per market and calendar date. Every flag and neighbouring settlement
-- day is computed by northstar.domain.calendar (ADR 007) and loaded as
-- reference data; this model only exposes it.
with days as (
    select
        calendar_day_id,
        market,
        calendar_date,
        is_trading_day,
        is_settlement_day,
        trading_holiday_name,
        settlement_holiday_name,
        previous_settlement_day,
        next_settlement_day,
        calendar_version,
        _ingestion_run_id,
        _ingested_at
    from {{ ref('stg_reference__market_calendar_days') }}
)

select
    calendar_day_id,
    market,
    calendar_date,
    is_trading_day,
    is_settlement_day,
    trading_holiday_name,
    settlement_holiday_name,
    previous_settlement_day,
    next_settlement_day,
    calendar_version,
    _ingestion_run_id,
    _ingested_at,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from days
