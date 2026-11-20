-- ADR 002: every posted journal line obeys the constraints that
-- ledger.journal_lines' CHECK and UNIQUE constraints enforce on Postgres
-- (northstar.infrastructure.postgres). Redshift has no CHECK constraints and does
-- not enforce UNIQUE, so on the cloud warehouse this test is the database-side
-- control (owner decision 2026-11-20). Returns each breaking line with the rule
-- it breaks (broken_rule).
with lines as (
    select * from {{ ref('stg_ledger__journal_lines') }}
),

repeated as (
    select
        journal_batch_id,
        line_number
    from lines
    group by journal_batch_id, line_number
    having count(*) > 1
),

breaks as (
    select
        journal_line_id,
        'line number must be 1 or more' as broken_rule
    from lines
    where line_number < 1

    union all
    select
        journal_line_id,
        'entry side must be DEBIT or CREDIT' as broken_rule
    from lines
    where entry_side not in ('DEBIT', 'CREDIT')

    union all
    select
        journal_line_id,
        'amount must be positive' as broken_rule
    from lines
    where amount <= 0

    union all
    select
        l.journal_line_id,
        'line number repeats within a batch' as broken_rule
    from lines as l
    inner join repeated as d
        on l.journal_batch_id = d.journal_batch_id and l.line_number = d.line_number
)

select * from breaks
