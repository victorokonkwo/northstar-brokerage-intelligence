-- The business dates daily facts and marts are reported for: every ledger
-- business day (XTSE settlement day, domain model §5) from the first to the
-- last posting date in the journal.
with bounds as (
    select
        min(posting_date) as first_date,
        max(posting_date) as last_date
    from {{ ref('fct_journal_lines') }}
)

select c.calendar_date as business_date
from {{ ref('dim_calendar') }} as c
cross join bounds as b
where
    c.market = 'XTSE'
    and c.is_settlement_day
    and c.calendar_date between b.first_date and b.last_date
