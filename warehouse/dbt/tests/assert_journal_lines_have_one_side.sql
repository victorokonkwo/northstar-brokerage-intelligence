-- Each journal line is a positive amount on exactly one side.
select
    journal_line_id,
    debit_amount,
    credit_amount
from {{ ref('fct_journal_lines') }}
where
    (debit_amount > 0) = (credit_amount > 0)
    or debit_amount < 0
    or credit_amount < 0
