-- Composite key: line numbers are unique within a batch (engineering standards §3.3).
select
    journal_batch_id,
    line_number,
    count(*) as line_count
from {{ ref('fct_journal_lines') }}
group by journal_batch_id, line_number
having count(*) > 1
