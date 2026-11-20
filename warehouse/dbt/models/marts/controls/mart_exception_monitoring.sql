-- KPI-03 (open exceptions and exposure) and KPI-04 (exceptions past SLA) per
-- business date x reconciliation x exception type x severity, from the current
-- lifecycle state. "Past SLA" counts open exceptions the sla_breach rule
-- escalated (Python owns the business-day age, ADR 007).
with exceptions as (
    select
        business_date,
        reconciliation,
        exception_type,
        severity,
        status,
        impact_cad,
        policy_version
    from {{ ref('fct_reconciliation_exceptions') }}
),

latest_run as (
    select
        as_of,
        run_id
    from {{ ref('stg_recon__runs') }}
    order by created_at desc, run_id desc
    limit 1
)

select
    e.business_date,
    e.reconciliation,
    e.exception_type,
    e.severity,
    count(*)::integer as exception_count,
    sum(case when e.status != 'RESOLVED' then 1 else 0 end)::integer as open_count,
    sum(case when e.status = 'ESCALATED' then 1 else 0 end)::integer as past_sla_open_count,
    sum(case when e.status = 'RESOLVED' then 1 else 0 end)::integer as resolved_count,
    coalesce(sum(case when e.status != 'RESOLVED' then abs(e.impact_cad) end), 0)::numeric(18, 2)
        as open_exposure_cad,
    max(e.policy_version) as policy_version,
    max(r.as_of) as reconciled_as_of,
    '{{ invocation_id }}'::text as _dbt_invocation_id
from exceptions as e
cross join latest_run as r
group by e.business_date, e.reconciliation, e.exception_type, e.severity
