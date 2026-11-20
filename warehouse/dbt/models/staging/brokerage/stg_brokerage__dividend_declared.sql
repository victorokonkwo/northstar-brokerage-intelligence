-- One row per dividend_declared event (an issuer's cash dividend declaration,
-- roadmap 5C-a). Rename and cast only. ex_date equals record_date (T+1, ADR 013).
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'dividend_declared'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'dividend_id') }} as dividend_id,
    {{ json_text('payload', 'instrument_id') }} as instrument_id,
    {{ json_text('payload', 'currency') }} as currency,
    ({{ json_text('payload', 'amount_per_share') }})::numeric(18, 8) as amount_per_share,
    ({{ json_text('payload', 'declaration_date') }})::date as declaration_date,
    ({{ json_text('payload', 'ex_date') }})::date as ex_date,
    ({{ json_text('payload', 'record_date') }})::date as record_date,
    ({{ json_text('payload', 'pay_date') }})::date as pay_date
from source
