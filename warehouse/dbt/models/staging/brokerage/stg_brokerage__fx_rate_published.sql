-- One row per fx_rate_published event (CAD per 1 USD). Rename and cast only.
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'fx_rate_published'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'base_currency') }} as base_currency,
    {{ json_text('payload', 'quote_currency') }} as quote_currency,
    ({{ json_text('payload', 'rate_date') }})::date as rate_date,
    ({{ json_text('payload', 'rate') }})::numeric(18, 10) as rate,
    {{ json_text('payload', 'rate_source') }} as rate_source,
    {{ json_text('payload', 'fx_policy_version') }} as fx_policy_version
from source
