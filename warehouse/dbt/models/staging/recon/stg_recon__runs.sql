-- One row per recon.runs row. Rename and cast only.
with source as (
    select * from {{ source('recon', 'runs') }}
)

select
    run_id,
    as_of,
    source_schema,
    policy_version,
    code_version,
    matches,
    exceptions,
    created_at
from source
