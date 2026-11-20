-- One row per option_expired event (roadmap 5C-b): a long option lapsed at
-- expiry and its contracts left the account. Rename and cast only.
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'option_expired'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'account_id') }} as account_id,
    {{ json_text('payload', 'instrument_id') }} as instrument_id,
    ({{ json_text('payload', 'contracts') }})::numeric(18, 8) as contracts,
    ({{ json_text('payload', 'expiry_date') }})::date as expiry_date,
    ({{ json_text('payload', 'underlying_close') }})::numeric(18, 8) as underlying_close,
    {{ json_text('payload', 'expiry_policy_version') }} as expiry_policy_version,
    {{ json_text('payload', 'reason') }} as expiry_reason
from source
