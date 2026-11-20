-- One row per trade_executed event (one fill). Rename and cast only.
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'trade_executed'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'execution_id') }} as execution_id,
    {{ json_text('payload', 'order_id') }} as order_id,
    {{ json_text('payload', 'account_id') }} as account_id,
    {{ json_text('payload', 'instrument_id') }} as instrument_id,
    {{ json_text('payload', 'market') }} as market,
    {{ json_text('payload', 'asset_class') }} as asset_class,
    {{ json_text('payload', 'side') }} as side,
    ({{ json_text('payload', 'quantity') }})::numeric(18, 8) as quantity,
    ({{ json_text('payload', 'price') }})::numeric(18, 8) as price,
    ({{ json_text('payload', 'multiplier') }})::integer as multiplier,
    {{ json_text('payload', 'currency') }} as currency,
    ({{ json_text('payload', 'commission') }})::numeric(18, 2) as commission,
    -- trade_executed v1 has no clearing_fee field; its contract fixes the fee at 0.00
    -- (northstar.domain.events.TradeExecutedPayload default), so this is not a fill-in.
    coalesce(
        ({{ json_text('payload', 'clearing_fee') }})::numeric(18, 2), 0.00
    )::numeric(18, 2) as clearing_fee,
    ({{ json_text('payload', 'trade_date') }})::date as trade_date,
    ({{ json_text('payload', 'settlement_date') }})::date as settlement_date,
    {{ json_text('payload', 'settlement_id') }} as settlement_id,
    {{ json_text('payload', 'policy_versions', 'fee_schedule') }} as fee_schedule_version,
    {{ json_text('payload', 'policy_versions', 'settlement_rules') }} as settlement_rules_version,
    {{ json_text('payload', 'policy_versions', 'calendar') }} as calendar_version
from source
