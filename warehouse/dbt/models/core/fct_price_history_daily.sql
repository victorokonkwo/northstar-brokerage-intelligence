-- One row per instrument and day with a close: the synthetic history before the
-- dataset window (simulation.history, roadmap 5A) followed by the window's own
-- closes (price_updated). Historical VaR reads daily returns from it (KPI-09,
-- KPI-10) and the backtest reads its prices (KPI-14). Nothing is carried forward
-- or filled: a day without a close has no row (W2.6).
with history as (
    select
        instrument_id,
        price_date,
        close_price,
        currency,
        'synthetic-history'::{{ string_type() }} as price_source,
        record_id as source_id
    from {{ ref('stg_external__price_history') }}
),

window_closes as (
    select
        instrument_id,
        price_date,
        close_price,
        currency,
        price_source,
        event_id as source_id
    from {{ ref('stg_brokerage__price_updated') }}
),

closes as (
    select * from history
    union all
    select * from window_closes
)

select
    instrument_id,
    price_date,
    close_price,
    currency,
    price_source,
    source_id as _source_id,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from closes
