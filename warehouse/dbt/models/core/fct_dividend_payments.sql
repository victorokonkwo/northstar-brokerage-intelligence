-- One row per dividend x entitled account (roadmap 5C-a, ADR 013): the client
-- cash credited on the pay date. Amounts come from the ledger, the system of
-- record (ADR 002): the dividend_paid batch's 2100 credit line per account.
with paid as (
    select
        event_id,
        dividend_id,
        instrument_id,
        amount_per_share,
        record_date,
        pay_date
    from {{ ref('stg_brokerage__dividend_paid') }}
),

credited as (
    select
        _source_event_id,
        client_account_id,
        currency,
        posting_date,
        credit_amount
    from {{ ref('fct_journal_lines') }}
    where
        source_event_type = 'dividend_paid'
        and gl_account = '2100'
)

select
    paid.dividend_id,
    credited.client_account_id as account_id,
    paid.instrument_id,
    paid.record_date,
    paid.pay_date,
    credited.posting_date,
    credited.currency,
    paid.amount_per_share,
    credited.credit_amount as amount,
    paid.event_id as _source_event_id,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from credited
inner join paid on credited._source_event_id = paid.event_id
