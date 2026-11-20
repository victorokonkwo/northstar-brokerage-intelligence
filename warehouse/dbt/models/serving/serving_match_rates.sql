-- KPI-02 as published: match rates by count and notional. One row per row of
-- mart_match_rates in each business date's currently PUBLISHED controls snapshot,
-- never a draft or a superseded version (FR-M2-09; W5: dashboards read published
-- results only). It adds the version and when it was published (FR-DASH-01). The
-- operations dashboard reads this view and calculates nothing
-- (docs/dashboards/operations.md).
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
    m.currency,
    m.execution_count,
    m.matched_count,
    m.match_rate,
    m.notional,
    m.matched_notional,
    m.notional_match_rate,
    v.snapshot_version,
    v.published_at
from {{ source('published_controls', 'mart_match_rates') }} as m
inner join current_versions as v
    on
        m.business_date = v.business_date
        and m.snapshot_version = v.snapshot_version
