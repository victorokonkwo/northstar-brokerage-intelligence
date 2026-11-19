-- INV-01 / KPI-01 (domain model §8, KPI dictionary): every journal batch
-- balances in every currency. Returns the violating (batch, currency) groups;
-- the test passes only when there are none.
select
    journal_batch_id,
    currency,
    sum(debit_amount) as total_debit,
    sum(credit_amount) as total_credit
from {{ ref('fct_journal_lines') }}
group by journal_batch_id, currency
having sum(debit_amount) <> sum(credit_amount)
