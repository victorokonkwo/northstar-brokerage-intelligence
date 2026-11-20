{{ config(
    materialized='incremental',
    incremental_strategy='rebuild',
    on_schema_change='fail',
    post_hook="{{ record_build() }}",
    meta={'rebuild': {
        'date': 'business_date',
        'inputs': [
            'stg_ledger__journal_lines',
        ],
    }},
) }}

-- depends_on: {{ ref('int_arrivals') }}

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
    -- numeric(38, 2), never bare numeric: Redshift reads that as numeric(18, 0)
    -- and rounds away the cents (docs/sources.md).
    sum(total_debit)::numeric(38, 2) as total_debit,
    sum(total_credit)::numeric(38, 2) as total_credit,
    sum(case when total_debit <> total_credit then 1 else 0 end)::bigint as unbalanced_batch_count,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from batches
{% if is_incremental() %}
    where {{ rebuild_filter('business_date') }}
{% endif %}
group by business_date, currency
