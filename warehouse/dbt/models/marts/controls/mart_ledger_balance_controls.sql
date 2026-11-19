-- KPI-01 control mart (KPI dictionary): ledger balance controls per business
-- date and currency. Grain: business_date x currency.
-- unbalanced_batch_count is KPI-01 and must be 0 (it is also enforced by the
-- test assert_balanced_journals).
with lines as (
    select
        journal_batch_id,
        business_date,
        currency,
        debit_amount,
        credit_amount
    from {{ ref('fct_journal_lines') }}
),

batches as (
    select
        business_date,
        currency,
        journal_batch_id,
        count(*) as line_count,
        sum(debit_amount) as total_debit,
        sum(credit_amount) as total_credit
    from lines
    group by business_date, currency, journal_batch_id
)

select
    business_date,
    currency,
    count(*)::bigint as batch_count,
    sum(line_count)::bigint as line_count,
    sum(total_debit)::numeric as total_debit,
    sum(total_credit)::numeric as total_credit,
    (count(*) filter (where total_debit <> total_credit))::bigint as unbalanced_batch_count,
    '{{ invocation_id }}'::text as _dbt_invocation_id
from batches
group by business_date, currency
