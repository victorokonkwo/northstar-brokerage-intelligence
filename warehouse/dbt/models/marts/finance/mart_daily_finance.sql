-- Daily revenue and direct costs per posting date x currency, from the journal
-- (ADR 002: the ledger is the system of record). Revenue accounts are
-- credit-normal (credits - debits), expense accounts debit-normal. FX
-- conversion revenue (4200) is always booked in CAD (OQ-01). Net contribution
-- is also translated to CAD at the applicable reference rate (INV-07).
with lines as (
    select
        posting_date,
        currency,
        gl_account,
        credit_amount - debit_amount as credit_net
    from {{ ref('fct_journal_lines') }}
    where gl_account in ('4100', '4200', '4300', '5100')
),

daily as (
    select
        l.posting_date as business_date,
        l.currency,
        sum(case when l.gl_account = '4100' then l.credit_net else 0 end) as commission_revenue,
        sum(case when l.gl_account = '4200' then l.credit_net else 0 end) as fx_conversion_revenue,
        sum(case when l.gl_account = '4300' then l.credit_net else 0 end)
            as margin_interest_revenue,
        sum(case when l.gl_account = '5100' then -l.credit_net else 0 end) as clearing_costs,
        count(*) as line_count
    from lines as l
    group by l.posting_date, l.currency
)

select
    d.business_date,
    d.currency,
    d.commission_revenue::numeric(18, 2) as commission_revenue,
    d.fx_conversion_revenue::numeric(18, 2) as fx_conversion_revenue,
    d.margin_interest_revenue::numeric(18, 2) as margin_interest_revenue,
    d.clearing_costs::numeric(18, 2) as clearing_costs,
    (
        d.commission_revenue + d.fx_conversion_revenue + d.margin_interest_revenue
        - d.clearing_costs
    )::numeric(18, 2) as net_contribution,
    case
        when d.currency = 'CAD'
            then (
                d.commission_revenue + d.fx_conversion_revenue + d.margin_interest_revenue
                - d.clearing_costs
            )::numeric(18, 2)
        else {{ round_half_even(
            '(d.commission_revenue + d.fx_conversion_revenue + d.margin_interest_revenue'
            ~ ' - d.clearing_costs) * fx.rate', 2) }}
    end as net_contribution_cad,
    case when d.currency = 'USD' then fx.rate end as fx_rate,
    case when d.currency = 'USD' then fx.fx_rate_date end as fx_rate_date,
    case when d.currency = 'USD' then fx.fx_policy_version end as fx_policy_version,
    d.line_count::integer as _journal_line_count,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from daily as d
left join {{ ref('int_fx_rates_applicable') }} as fx on d.business_date = fx.calendar_date
