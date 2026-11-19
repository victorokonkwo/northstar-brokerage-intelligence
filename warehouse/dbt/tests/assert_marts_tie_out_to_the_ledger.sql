-- INV-10: each gate mart's totals equal the journal lines (or facts) they are
-- built from, recomputed here independently. Returns each check that differs.
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

checks as (
    -- Client cash: each account's last closing balance = all its 2100 lines.
    select
        'client_cash ' || c.account_id || ' ' || c.currency as check_name,
        c.closing_balance as mart_value,
        (
            select coalesce(sum(l.credit_amount - l.debit_amount), 0) from ledger as l
            where
                l.gl_account = '2100'
                and l.client_account_id = c.account_id
                and l.currency = c.currency
        ) as ledger_value
    from {{ ref('mart_daily_client_cash') }} as c
    where c.business_date = (select max(d.business_date) from {{ ref('int_reporting_dates') }} as d)

    union all

    -- Finance: each revenue and cost column = its GL account, per currency.
    select
        'finance ' || f.gl_account || ' ' || f.currency as check_name,
        f.mart_value,
        (
            select
                coalesce(sum(case
                    when f.gl_account = '5100'
                        then l.debit_amount - l.credit_amount
                    else l.credit_amount - l.debit_amount
                end), 0)
            from ledger as l
            where l.gl_account = f.gl_account and l.currency = f.currency
        ) as ledger_value
    from (
        select
            '4100' as gl_account,
            currency,
            sum(commission_revenue) as mart_value
        from {{ ref('mart_daily_finance') }}
        group by currency
        union all
        select
            '4200' as gl_account,
            currency,
            sum(fx_conversion_revenue) as mart_value
        from {{ ref('mart_daily_finance') }}
        group by currency
        union all
        select
            '4300' as gl_account,
            currency,
            sum(margin_interest_revenue) as mart_value
        from {{ ref('mart_daily_finance') }}
        group by currency
        union all
        select
            '5100' as gl_account,
            currency,
            sum(clearing_costs) as mart_value
        from {{ ref('mart_daily_finance') }}
        group by currency
    ) as f

    union all

    -- Executions: commission and clearing fees = the trade lines on 4100 and 5100.
    select
        'executions commission ' || x.currency as check_name,
        x.commission as mart_value,
        (
            select coalesce(sum(l.credit_amount), 0) from ledger as l
            where
                l.gl_account = '4100'
                and l.source_event_type = 'trade_executed'
                and l.currency = x.currency
        ) as ledger_value
    from (
        select
            currency,
            sum(commission) as commission
        from {{ ref('mart_daily_executions') }}
        group by currency
    ) as x

    union all

    select
        'executions clearing_fee ' || x.currency as check_name,
        x.clearing_fee as mart_value,
        (
            select coalesce(sum(l.debit_amount), 0) from ledger as l
            where
                l.gl_account = '5100'
                and l.source_event_type = 'trade_executed'
                and l.currency = x.currency
        ) as ledger_value
    from (
        select
            currency,
            sum(clearing_fee) as clearing_fee
        from {{ ref('mart_daily_executions') }}
        group by currency
    ) as x

    union all

    -- Positions: each account-day total = the sum of its priced positions.
    select
        'positions ' || m.account_id || ' ' || m.business_date as check_name,
        m.market_value_cad as mart_value,
        (
            select sum(p.market_value_cad) from {{ ref('fct_positions_daily') }} as p
            where p.account_id = m.account_id and p.business_date = m.business_date
        ) as ledger_value
    from {{ ref('mart_daily_positions') }} as m
    where not m.has_missing_price
)

select * from checks
where mart_value is distinct from ledger_value
