-- Daily execution activity per trade date x market x currency. Amounts are
-- the journal lines Python posted for each execution (int_execution_amounts),
-- so they tie out to the ledger (INV-10).
with executions as (
    select
        execution_id,
        order_id,
        trade_date,
        market,
        currency,
        gross_amount,
        commission,
        clearing_fee,
        journal_line_count
    from {{ ref('int_execution_amounts') }}
)

select
    trade_date as business_date,
    market,
    currency,
    count(*)::integer as execution_count,
    count(distinct order_id)::integer as order_count,
    sum(gross_amount)::numeric(18, 2) as gross_amount,
    sum(commission)::numeric(18, 2) as commission,
    sum(clearing_fee)::numeric(18, 2) as clearing_fee,
    sum(journal_line_count)::integer as _journal_line_count,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from executions
group by trade_date, market, currency
