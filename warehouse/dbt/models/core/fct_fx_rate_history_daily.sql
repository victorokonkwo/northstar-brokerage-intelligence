-- One row per USD/CAD reference rate date: the synthetic history before the
-- dataset window (simulation.history, roadmap 5A) followed by the window's own
-- published rates. Historical VaR translates USD positions with it (KPI-09,
-- KPI-10). Rates are CAD per 1 USD; nothing is carried forward or filled.
with history as (
    select
        base_currency,
        quote_currency,
        rate_date,
        rate,
        'synthetic-history'::{{ string_type() }} as rate_source,
        record_id as source_id
    from {{ ref('stg_external__fx_rate_history') }}
),

window_rates as (
    select
        base_currency,
        quote_currency,
        rate_date,
        rate,
        rate_source,
        event_id as source_id
    from {{ ref('stg_brokerage__fx_rate_published') }}
),

rates as (
    select * from history
    union all
    select * from window_rates
)

select
    base_currency,
    quote_currency,
    rate_date,
    rate,
    rate_source,
    source_id as _source_id,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from rates
