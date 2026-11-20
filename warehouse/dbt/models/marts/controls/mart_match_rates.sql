-- KPI-02: executions matched to a clearing confirmation (by any rule) / all
-- executions, per trade date x currency, by count and by gross notional
-- (from the posted journal lines). Ratios are rounded half-even to 6 places.
with executions as (
    select
        execution_id,
        trade_date,
        currency,
        gross_amount
    from {{ ref('int_execution_amounts') }}
),

matched as (
    select distinct internal_ref as execution_id
    from {{ ref('stg_recon__match_results') }}
    where reconciliation = 'executions'
),

daily as (
    select
        e.trade_date as business_date,
        e.currency,
        count(*) as execution_count,
        count(m.execution_id) as matched_count,
        sum(abs(e.gross_amount)) as notional,
        coalesce(sum(case when m.execution_id is not null then abs(e.gross_amount) end), 0)
            as matched_notional
    from executions as e
    left join matched as m on e.execution_id = m.execution_id
    group by e.trade_date, e.currency
)

select
    business_date,
    currency,
    execution_count::integer as execution_count,
    matched_count::integer as matched_count,
    {{ round_half_even('matched_count::numeric / execution_count', 6) }} as match_rate,
    notional::numeric(18, 2) as notional,
    matched_notional::numeric(18, 2) as matched_notional,
    {{ round_half_even('matched_notional / nullif(notional, 0)', 6) }} as notional_match_rate,
    '{{ invocation_id }}'::text as _dbt_invocation_id
from daily
