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
    payload ->> 'execution_id' as execution_id,
    payload ->> 'order_id' as order_id,
    payload ->> 'account_id' as account_id,
    payload ->> 'instrument_id' as instrument_id,
    payload ->> 'market' as market,
    payload ->> 'asset_class' as asset_class,
    payload ->> 'side' as side,
    (payload ->> 'quantity')::numeric(18, 8) as quantity,
    (payload ->> 'price')::numeric(18, 8) as price,
    (payload ->> 'multiplier')::integer as multiplier,
    payload ->> 'currency' as currency,
    (payload ->> 'commission')::numeric(18, 2) as commission,
    -- trade_executed v1 has no clearing_fee field; its contract fixes the fee at 0.00
    -- (northstar.domain.events.TradeExecutedPayload default), so this is not a fill-in.
    coalesce((payload ->> 'clearing_fee')::numeric(18, 2), 0.00) as clearing_fee,
    (payload ->> 'trade_date')::date as trade_date,
    (payload ->> 'settlement_date')::date as settlement_date,
    payload ->> 'settlement_id' as settlement_id,
    payload -> 'policy_versions' ->> 'fee_schedule' as fee_schedule_version,
    payload -> 'policy_versions' ->> 'settlement_rules' as settlement_rules_version,
    payload -> 'policy_versions' ->> 'calendar' as calendar_version
from source
