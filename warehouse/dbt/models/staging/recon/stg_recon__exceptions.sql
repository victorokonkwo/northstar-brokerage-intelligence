-- One row per recon.exceptions row. Rename and cast only.
with source as (
    select * from {{ source('recon', 'exceptions') }}
)

select
    exception_id,
    reconciliation,
    exception_type,
    severity,
    reference,
    business_date,
    impact_amount,
    impact_currency,
    impact_basis,
    impact_cad,
    fx_rate_date,
    fx_policy_version,
    external_record_ids,
    detail,
    policy_version,
    run_id,
    _ingested_at
from source
