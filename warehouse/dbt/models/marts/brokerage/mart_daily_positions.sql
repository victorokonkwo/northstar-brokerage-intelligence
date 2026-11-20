{{ config(
    materialized='incremental',
    incremental_strategy='rebuild',
    on_schema_change='fail',
    post_hook="{{ record_build() }}",
    meta={'rebuild': {
        'date': 'business_date',
        'inputs': [
            'stg_brokerage__trade_executed',
            'stg_brokerage__trade_settled',
            'stg_brokerage__price_updated',
            'stg_brokerage__instrument_listed',
            'stg_brokerage__fx_rate_published',
            'stg_ledger__journal_lines',
            'stg_reference__market_calendar_days',
        ],
    }},
) }}

-- depends_on: {{ ref('int_arrivals') }}

-- Portfolio value per account x reporting date, summed from fct_positions_daily
-- (whose market values are already rounded once). A total is null when any
-- position has no price (INV-08: never valued with a missing price), and the
-- stale and missing flags propagate from the positions.
with positions as (
    select
        account_id,
        business_date,
        market_value_cad,
        is_price_missing,
        is_price_stale
    from {{ ref('fct_positions_daily') }}
)

select
    account_id,
    business_date,
    count(*)::integer as position_count,
    case
        when bool_or(is_price_missing) then null
        else sum(market_value_cad)
    end::numeric(18, 2) as market_value_cad,
    bool_or(is_price_missing) as has_missing_price,
    bool_or(is_price_stale) as has_stale_price,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from positions
{% if is_incremental() %}
    where {{ rebuild_filter('business_date') }}
{% endif %}
group by account_id, business_date
