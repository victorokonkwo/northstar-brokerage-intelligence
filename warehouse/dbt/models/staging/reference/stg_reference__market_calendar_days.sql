-- One row per market and calendar date, as northstar.domain.calendar computes it
-- (the calendar logic's single owner, ADR 007). Rename and cast only.
with source as (
    select * from {{ source('reference', 'market_calendar_days') }}
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
    _ingested_at
from source
