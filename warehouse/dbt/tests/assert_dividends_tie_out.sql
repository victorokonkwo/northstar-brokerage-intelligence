-- Each dividend payment ties out three ways (roadmap 5C-a, ADR 013): the client
-- credits in fct_dividend_payments sum to the payment's total, and the bank
-- (GL 1000) receives exactly that total in one line. Returns the dividends that
-- don't tie.
with credits as (
    select
        dividend_id,
        sum(amount) as credited
    from {{ ref('fct_dividend_payments') }}
    group by dividend_id
),

bank as (
    select
        p.dividend_id,
        sum(j.debit_amount - j.credit_amount) as received,
        count(*) as bank_lines
    from {{ ref('fct_journal_lines') }} as j
    inner join {{ ref('stg_brokerage__dividend_paid') }} as p on j._source_event_id = p.event_id
    where j.gl_account = '1000'
    group by p.dividend_id
)

select
    p.dividend_id,
    p.total_amount,
    c.credited,
    b.received,
    b.bank_lines
from {{ ref('stg_brokerage__dividend_paid') }} as p
left join credits as c on p.dividend_id = c.dividend_id
left join bank as b on p.dividend_id = b.dividend_id
where
    {{ distinct_from('c.credited', 'p.total_amount') }}
    or {{ distinct_from('b.received', 'p.total_amount') }}
    or {{ distinct_from('b.bank_lines', '1') }}
