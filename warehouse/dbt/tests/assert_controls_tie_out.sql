-- The controls marts tie out to what they are built from (INV-10):
-- exception counts and open exposure to fct_reconciliation_exceptions, match
-- counts to the executions and the stored matches, and the cash roll-forward's
-- last balances to all GL 1000 lines and all bank lines. Returns each check
-- that differs.
with exceptions as (
    select
        count(*) as exception_count,
        coalesce(sum(case when status != 'RESOLVED' then 1 else 0 end), 0) as open_count,
        coalesce(sum(case when status != 'RESOLVED' then abs(impact_cad) end), 0)
            as open_exposure_cad
    from {{ ref('fct_reconciliation_exceptions') }}
),

monitoring as (
    select
        coalesce(sum(exception_count), 0) as exception_count,
        coalesce(sum(open_count), 0) as open_count,
        coalesce(sum(open_exposure_cad), 0) as open_exposure_cad
    from {{ ref('mart_exception_monitoring') }}
),

rates as (
    select
        coalesce(sum(execution_count), 0) as execution_count,
        coalesce(sum(matched_count), 0) as matched_count
    from {{ ref('mart_match_rates') }}
),

matched as (
    select count(distinct internal_ref) as matched_count
    from {{ ref('stg_recon__match_results') }}
    where reconciliation = 'executions'
),

last_cash as (
    select
        currency,
        gl_cash_balance,
        bank_balance
    from {{ ref('mart_cash_breaks') }}
    where business_date = (select max(d.business_date) from {{ ref('int_reporting_dates') }} as d)
),

ledger_cash as (
    select
        currency,
        sum(debit_amount - credit_amount) as balance
    from {{ ref('fct_journal_lines') }}
    where gl_account = '1000'
    group by currency
),

bank_cash as (
    select
        currency,
        sum(amount) as balance
    from {{ ref('stg_external__bank_statement_lines') }}
    group by currency
),

checks as (
    select
        'exception_count' as check_name,
        m.exception_count as mart_value,
        e.exception_count as source_value
    from monitoring as m cross join exceptions as e
    union all
    select
        'open_count' as check_name,
        m.open_count as mart_value,
        e.open_count as source_value
    from monitoring as m cross join exceptions as e
    union all
    select
        'open_exposure_cad' as check_name,
        m.open_exposure_cad as mart_value,
        e.open_exposure_cad as source_value
    from monitoring as m cross join exceptions as e
    union all
    select
        'execution_count' as check_name,
        r.execution_count as mart_value,
        (select count(*) from {{ ref('int_execution_amounts') }}) as source_value
    from rates as r
    union all
    select
        'matched_count' as check_name,
        r.matched_count as mart_value,
        m.matched_count as source_value
    from rates as r cross join matched as m
    union all
    select
        'gl_cash ' || c.currency as check_name,
        c.gl_cash_balance as mart_value,
        coalesce(l.balance, 0) as source_value
    from last_cash as c left join ledger_cash as l on c.currency = l.currency
    union all
    select
        'bank_cash ' || c.currency as check_name,
        c.bank_balance as mart_value,
        coalesce(b.balance, 0) as source_value
    from last_cash as c left join bank_cash as b on c.currency = b.currency
)

select
    check_name,
    mart_value,
    source_value
from checks
where mart_value is distinct from source_value
