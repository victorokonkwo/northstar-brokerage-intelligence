-- INV-10: each gate mart's totals equal the journal lines (or facts) they are
-- built from, recomputed here independently. Each side is aggregated once
-- per key and the totals are joined, so the test stays linear in the data
-- (a per-row subquery did not finish at load-test size). Returns each check
-- that differs, including a key present on one side only.
with ledger as (
    select
        gl_account,
        currency,
        client_account_id,
        source_event_type,
        debit_amount,
        credit_amount
    from {{ ref('fct_journal_lines') }}
),

last_date as (
    select max(business_date) as business_date from {{ ref('int_reporting_dates') }}
),

-- Client cash: each account's last closing balance = all its 2100 lines.
cash_mart as (
    select
        c.account_id,
        c.currency,
        c.closing_balance
    from {{ ref('mart_daily_client_cash') }} as c
    inner join last_date as d on c.business_date = d.business_date
),

cash_ledger as (
    select
        client_account_id as account_id,
        currency,
        sum(credit_amount - debit_amount) as balance
    from ledger
    where gl_account = '2100'
    group by client_account_id, currency
),

-- Finance: each revenue and cost column = its GL account, per currency.
finance_mart as (
    select
        currency,
        sum(commission_revenue) as gl_4100,
        sum(fx_conversion_revenue) as gl_4200,
        sum(margin_interest_revenue) as gl_4300,
        sum(clearing_costs) as gl_5100
    from {{ ref('mart_daily_finance') }}
    group by currency
),

finance_ledger as (
    select
        currency,
        sum(case when gl_account = '4100' then credit_amount - debit_amount else 0 end) as gl_4100,
        sum(case when gl_account = '4200' then credit_amount - debit_amount else 0 end) as gl_4200,
        sum(case when gl_account = '4300' then credit_amount - debit_amount else 0 end) as gl_4300,
        sum(case when gl_account = '5100' then debit_amount - credit_amount else 0 end) as gl_5100
    from ledger
    where gl_account in ('4100', '4200', '4300', '5100')
    group by currency
),

-- Executions: commission and clearing fees = the trade lines on 4100 and 5100.
executions_mart as (
    select
        currency,
        sum(commission) as commission,
        sum(clearing_fee) as clearing_fee
    from {{ ref('mart_daily_executions') }}
    group by currency
),

executions_ledger as (
    select
        currency,
        sum(case when gl_account = '4100' then credit_amount else 0 end) as commission,
        sum(case when gl_account = '5100' then debit_amount else 0 end) as clearing_fee
    from ledger
    where source_event_type = 'trade_executed' and gl_account in ('4100', '5100')
    group by currency
),

-- Positions: each account-day total = the sum of its priced positions.
positions_facts as (
    select
        account_id,
        business_date,
        sum(market_value_cad) as market_value_cad
    from {{ ref('fct_positions_daily') }}
    group by account_id, business_date
),

finance_accounts as (
    select '4100' as gl_account
    union all
    select '4200' as gl_account
    union all
    select '4300' as gl_account
    union all
    select '5100' as gl_account
),

checks as (
    select
        'client_cash ' || coalesce(m.account_id, l.account_id) || ' '
        || coalesce(m.currency, l.currency) as check_name,
        m.closing_balance as mart_value,
        coalesce(l.balance, 0) as ledger_value
    from cash_mart as m
    full outer join cash_ledger as l
        on m.account_id = l.account_id and m.currency = l.currency

    union all

    select
        'finance ' || g.gl_account || ' ' || coalesce(m.currency, l.currency) as check_name,
        case g.gl_account
            when '4100' then m.gl_4100 when '4200' then m.gl_4200
            when '4300' then m.gl_4300 else m.gl_5100
        end as mart_value,
        case g.gl_account
            when '4100' then l.gl_4100 when '4200' then l.gl_4200
            when '4300' then l.gl_4300 else l.gl_5100
        end as ledger_value
    from finance_mart as m
    full outer join finance_ledger as l on m.currency = l.currency
    cross join finance_accounts as g

    union all

    select
        'executions commission ' || coalesce(m.currency, l.currency) as check_name,
        m.commission as mart_value,
        l.commission as ledger_value
    from executions_mart as m
    full outer join executions_ledger as l on m.currency = l.currency

    union all

    select
        'executions clearing_fee ' || coalesce(m.currency, l.currency) as check_name,
        m.clearing_fee as mart_value,
        l.clearing_fee as ledger_value
    from executions_mart as m
    full outer join executions_ledger as l on m.currency = l.currency

    union all

    select
        'positions ' || m.account_id || ' ' || m.business_date as check_name,
        m.market_value_cad as mart_value,
        p.market_value_cad as ledger_value
    from {{ ref('mart_daily_positions') }} as m
    left join positions_facts as p
        on m.account_id = p.account_id and m.business_date = p.business_date
    where not m.has_missing_price
)

select
    check_name,
    mart_value,
    ledger_value
from checks
where {{ distinct_from('mart_value', 'ledger_value') }}
