-- KPI-06: the firm's bank-account cash per reporting date x currency, the
-- ledger (GL 1000) against the bank statement, rolled forward from their
-- movements. A late settlement booked on its contractual date but paid on its
-- actual date is an explained timing difference while it is in transit;
-- everything else is unexplained (target: 0).
with ledger_moves as (
    select
        posting_date as move_date,
        currency,
        sum(debit_amount - credit_amount) as amount
    from {{ ref('fct_journal_lines') }}
    where gl_account = '1000'
    group by posting_date, currency
),

bank_moves as (
    select
        value_date as move_date,
        currency,
        sum(amount) as amount
    from {{ ref('stg_external__bank_statement_lines') }}
    group by value_date, currency
),

late as (
    select
        c.contractual_settlement_date,
        c.actual_settlement_date,
        b.currency,
        b.amount
    from {{ ref('stg_external__settlement_confirmations') }} as c
    inner join {{ ref('stg_external__bank_statement_lines') }} as b
        on c.broker_reference = b.reference
    where c.actual_settlement_date > c.contractual_settlement_date
),

currencies as (
    select 'CAD' as currency
    union all
    select 'USD' as currency
),

grid as (
    select
        d.business_date,
        c.currency
    from {{ ref('int_reporting_dates') }} as d
    cross join currencies as c
),

gl as (
    select
        g.business_date,
        g.currency,
        sum(l.amount) as gl_cash_balance
    from grid as g
    inner join ledger_moves as l
        on g.currency = l.currency and g.business_date >= l.move_date
    group by g.business_date, g.currency
),

bank as (
    select
        g.business_date,
        g.currency,
        sum(b.amount) as bank_balance
    from grid as g
    inner join bank_moves as b
        on g.currency = b.currency and g.business_date >= b.move_date
    group by g.business_date, g.currency
),

in_transit as (
    select
        g.business_date,
        g.currency,
        sum(t.amount) as explained_timing
    from grid as g
    inner join late as t
        on
            g.currency = t.currency
            and g.business_date >= t.contractual_settlement_date
            and g.business_date < t.actual_settlement_date
    group by g.business_date, g.currency
),

balances as (
    select
        g.business_date,
        g.currency,
        coalesce(l.gl_cash_balance, 0) as gl_cash_balance,
        coalesce(b.bank_balance, 0) as bank_balance,
        coalesce(t.explained_timing, 0) as explained_timing
    from grid as g
    left join gl as l on g.business_date = l.business_date and g.currency = l.currency
    left join bank as b on g.business_date = b.business_date and g.currency = b.currency
    left join in_transit as t on g.business_date = t.business_date and g.currency = t.currency
)

select
    business_date,
    currency,
    gl_cash_balance::numeric(18, 2) as gl_cash_balance,
    bank_balance::numeric(18, 2) as bank_balance,
    (gl_cash_balance - bank_balance)::numeric(18, 2) as difference,
    explained_timing::numeric(18, 2) as explained_timing,
    (gl_cash_balance - bank_balance - explained_timing)::numeric(18, 2) as unexplained,
    abs(gl_cash_balance - bank_balance - explained_timing)::numeric(18, 2) as cash_break_amount,
    '{{ invocation_id }}'::text as _dbt_invocation_id
from balances
