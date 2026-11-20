-- One row per recon.exception_transitions row. Rename and cast only.
with source as (
    select * from {{ source('recon', 'exception_transitions') }}
)

select
    transition_id,
    exception_id,
    sequence,
    from_status,
    to_status,
    actor,
    reason,
    resolution_type,
    changed_at,
    run_id
from source
