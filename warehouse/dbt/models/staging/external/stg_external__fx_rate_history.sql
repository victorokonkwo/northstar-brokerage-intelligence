-- One row per synthetic historical USD/CAD reference rate (rate date), before
-- the dataset window (roadmap 5A; simulation.history). Rename and cast only.
with source as (
    select * from {{ source('external', 'external_records') }}
    where record_set = 'fx_rate_history'
)

select
    record_id,
    event_date,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'base_currency') }} as base_currency,
    {{ json_text('payload', 'quote_currency') }} as quote_currency,
    ({{ json_text('payload', 'rate_date') }})::date as rate_date,
    ({{ json_text('payload', 'rate') }})::numeric(18, 10) as rate
from source
