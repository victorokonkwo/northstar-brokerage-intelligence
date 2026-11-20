-- One row per option_exercised event (roadmap 5C-b, ADR 013): the option's
-- contracts leave the account and the underlying is bought (call) or sold (put)
-- at the strike, settling on settlement_date. Rename and cast only.
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'option_exercised'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'exercise_id') }} as exercise_id,
    {{ json_text('payload', 'settlement_id') }} as settlement_id,
    {{ json_text('payload', 'account_id') }} as account_id,
    {{ json_text('payload', 'option_instrument_id') }} as option_instrument_id,
    {{ json_text('payload', 'underlying_instrument_id') }} as underlying_instrument_id,
    {{ json_text('payload', 'right') }} as option_right,
    ({{ json_text('payload', 'contracts') }})::numeric(18, 8) as contracts,
    ({{ json_text('payload', 'multiplier') }})::integer as multiplier,
    ({{ json_text('payload', 'shares') }})::numeric(18, 8) as underlying_quantity,
    ({{ json_text('payload', 'strike') }})::numeric(18, 8) as strike,
    ({{ json_text('payload', 'strike_amount') }})::numeric(18, 2) as strike_amount,
    {{ json_text('payload', 'currency') }} as currency,
    ({{ json_text('payload', 'exercise_date') }})::date as exercise_date,
    ({{ json_text('payload', 'settlement_date') }})::date as settlement_date,
    {{ json_text('payload', 'reason') }} as exercise_reason,
    ({{ json_text('payload', 'underlying_close') }})::numeric(18, 8) as underlying_close,
    {{ json_text('payload', 'expiry_policy_version') }} as expiry_policy_version
from source
