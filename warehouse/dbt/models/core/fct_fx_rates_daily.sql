-- One row per published reference rate (currency pair x rate date). The source
-- is the simulator's synthetic USD/CAD path (owner decision 2026-11-19), not
-- Bank of Canada data. Rates are quote currency per 1 unit of base.
with published as (
    select
        base_currency,
        quote_currency,
        rate_date,
        rate,
        rate_source,
        fx_policy_version,
        event_timestamp as published_at,
        event_id,
        schema_version,
        _ingestion_run_id,
        _ingested_at
    from {{ ref('stg_brokerage__fx_rate_published') }}
)

select
    base_currency,
    quote_currency,
    rate_date,
    rate,
    rate_source,
    fx_policy_version,
    published_at,
    event_id as _source_event_id,
    _ingestion_run_id,
    _ingested_at,
    schema_version as _schema_version,
    '{{ invocation_id }}'::text as _dbt_invocation_id
from published
