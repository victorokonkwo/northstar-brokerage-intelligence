-- KPI-03 and KPI-04 as published: open exceptions, exposure in CAD, ageing past SLA,
-- and resolutions. One row per row of mart_exception_monitoring in each business
-- date's currently PUBLISHED controls snapshot, never a draft or a superseded version
-- (FR-M2-09; W5: dashboards read published results only). It adds the version and when
-- it was published (FR-DASH-01). The operations dashboard reads this view and
-- calculates nothing (docs/dashboards/operations.md).
with current_versions as (
    select
        business_date,
        snapshot_version,
        published_at
    from {{ source('published_controls', 'snapshot_timeline') }}
    where status = 'PUBLISHED'
)

select
    m.business_date,
    m.reconciliation,
    m.exception_type,
    m.severity,
    m.exception_count,
    m.open_count,
    m.past_sla_open_count,
    m.resolved_count,
    m.open_exposure_cad,
    m.policy_version,
    m.reconciled_as_of,
    v.snapshot_version,
    v.published_at
from {{ source('published_controls', 'mart_exception_monitoring') }} as m
inner join current_versions as v
    on
        m.business_date = v.business_date
        and m.snapshot_version = v.snapshot_version
