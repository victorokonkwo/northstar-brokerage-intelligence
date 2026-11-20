{{ config(
    materialized='incremental',
    incremental_strategy='rebuild',
    on_schema_change='fail',
    post_hook="{{ record_build() }}",
    meta={'rebuild': {
        'date': 'business_date',
        'inputs': [
            'stg_ledger__journal_lines',
            'stg_brokerage__fx_rate_published',
            'stg_reference__market_calendar_days',
        ],
    }},
) }}

-- depends_on: {{ ref('int_arrivals') }}

-- Client cash per account x currency x reporting date, from the GL 2100
-- sub-ledger (INV-05). Client cash is a liability: credits add to the client's
-- balance and debits reduce it, so a negative closing balance is a margin loan.
-- One row for every reporting date from the account's first 2100 line, so a
-- day without activity still shows its balance. USD balances are translated to
-- CAD at the applicable reference rate (INV-07), rounded half-even once.
with lines as (
    select
        client_account_id as account_id,
        currency,
        posting_date,
        credit_amount,
        debit_amount
    from {{ ref('fct_journal_lines') }}
    where gl_account = '2100'
),

daily as (
    select
        account_id,
        currency,
        posting_date,
        sum(credit_amount) as credits,
        sum(debit_amount) as debits,
        count(*) as line_count
    from lines
    group by account_id, currency, posting_date
),

firsts as (
    select
        account_id,
        currency,
        min(posting_date) as first_date
    from daily
    group by account_id, currency
),

grid as (
    select
        f.account_id,
        f.currency,
        d.business_date
    from firsts as f
    inner join {{ ref('int_reporting_dates') }} as d on f.first_date <= d.business_date
),

balances as (
    select
        g.account_id,
        g.currency,
        g.business_date,
        coalesce(a.credits, 0) as credits,
        coalesce(a.debits, 0) as debits,
        coalesce(a.line_count, 0) as line_count,
        sum(coalesce(a.credits, 0) - coalesce(a.debits, 0)) over (
            partition by g.account_id, g.currency
            order by g.business_date
            rows between unbounded preceding and current row
        ) as closing_balance
    from grid as g
    left join daily as a
        on
            g.account_id = a.account_id
            and g.currency = a.currency
            and g.business_date = a.posting_date
)

select
    b.account_id,
    b.currency,
    b.business_date,
    (b.closing_balance - b.credits + b.debits)::numeric(18, 2) as opening_balance,
    b.credits::numeric(18, 2) as credits,
    b.debits::numeric(18, 2) as debits,
    b.closing_balance::numeric(18, 2) as closing_balance,
    case
        when b.currency = 'CAD' then b.closing_balance::numeric(18, 2)
        else {{ round_half_even('b.closing_balance * fx.rate', 2) }}
    end as closing_balance_cad,
    (case when b.currency = 'USD' then fx.rate end)::numeric(18, 10) as fx_rate,
    case when b.currency = 'USD' then fx.fx_rate_date end as fx_rate_date,
    case when b.currency = 'USD' then fx.fx_policy_version end as fx_policy_version,
    b.line_count::integer as _journal_line_count,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from balances as b
left join {{ ref('int_fx_rates_applicable') }} as fx on b.business_date = fx.calendar_date
{% if is_incremental() %}
    where {{ rebuild_filter('b.business_date') }}
{% endif %}
