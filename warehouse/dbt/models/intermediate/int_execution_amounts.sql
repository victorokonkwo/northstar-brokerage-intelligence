-- Each execution's gross amount, commission, and clearing fee, read from the
-- journal lines Python posted for it (ADR 007: no recomputation). A buy credits
-- the gross to 2200 and a sell debits it to 1200; the clearing fee is a 5100
-- debit with a matching 2200 credit, so it is taken out of the 2200 total.
with lines as (
    select
        _source_event_id as event_id,
        gl_account,
        debit_amount,
        credit_amount,
        currency
    from {{ ref('fct_journal_lines') }}
    where source_event_type = 'trade_executed'
),

per_event as (
    select
        event_id,
        min(currency) as currency,
        sum(case when gl_account = '1200' then debit_amount else 0 end)
        + sum(case when gl_account = '2200' then credit_amount else 0 end)
        - sum(case when gl_account = '5100' then debit_amount else 0 end) as gross_amount,
        sum(case when gl_account = '4100' then credit_amount else 0 end) as commission,
        sum(case when gl_account = '5100' then debit_amount else 0 end) as clearing_fee,
        count(*) as line_count
    from lines
    group by event_id
)

select
    e.execution_id,
    e.trade_date,
    e.market,
    e.currency,
    e.order_id,
    p.gross_amount::numeric(18, 2) as gross_amount,
    p.commission::numeric(18, 2) as commission,
    p.clearing_fee::numeric(18, 2) as clearing_fee,
    p.line_count as journal_line_count
from {{ ref('fct_executions') }} as e
inner join per_event as p on e._source_event_id = p.event_id
