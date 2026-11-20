-- Each raw record is recorded in int_arrivals exactly once (its grain), so no
-- record is counted as new by two builds. Returns each duplicate.
select
    source_model,
    record_id,
    count(*) as recorded
from {{ ref('int_arrivals') }}
group by source_model, record_id
having count(*) > 1
