{{ config(
    materialized='incremental',
    incremental_strategy='rebuild',
    on_schema_change='fail',
    post_hook="{{ record_build() }}",
    meta={'rebuild': {
        'key': 'settlement_id',
        'arrival': 'settlement_id',
        'inputs': [
            'stg_brokerage__trade_executed',
            'stg_brokerage__trade_settled',
        ],
    }},
) }}

-- depends_on: {{ ref('int_arrivals') }}

-- One row per settlement obligation: an order's executions on one trade date
-- (INV-06). Built from the executions, so an obligation exists from its first
-- fill. It is SETTLED once its trade_settled event arrives, PENDING before.
-- The amount is posted by Python (ADR 007) and known only from trade_settled,
-- so it is null while PENDING rather than recomputed here.
with executions as (
    select
        settlement_id,
        order_id,
        account_id,
        instrument_id,
        market,
        side,
        currency,
        trade_date,
        settlement_date,
        quantity
    from {{ ref('stg_brokerage__trade_executed') }}
),

obligations as (
    select
        settlement_id,
        min(order_id) as order_id,
        min(account_id) as account_id,
        min(instrument_id) as instrument_id,
        min(market) as market,
        min(side) as side,
        min(currency) as currency,
        min(trade_date) as trade_date,
        min(settlement_date) as contractual_settlement_date,
        sum(quantity) as executed_quantity,
        count(*) as execution_count
    from executions
    group by settlement_id
),

settled as (
    select
        settlement_id,
        quantity,
        amount,
        event_timestamp as settled_at,
        event_id,
        schema_version,
        _ingestion_run_id,
        _ingested_at
    from {{ ref('stg_brokerage__trade_settled') }}
)

select
    o.settlement_id,
    o.order_id,
    o.account_id,
    o.instrument_id,
    o.market,
    o.side,
    o.currency,
    o.trade_date,
    o.contractual_settlement_date,
    o.executed_quantity::numeric(18, 8) as executed_quantity,
    o.execution_count::integer as execution_count,
    s.quantity as settled_quantity,
    s.amount as settled_amount,
    s.settled_at,
    case when s.settlement_id is null then 'PENDING' else 'SETTLED' end as settlement_status,
    s.event_id as _source_event_id,
    s._ingestion_run_id,
    s._ingested_at,
    s.schema_version as _schema_version,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from obligations as o
left join settled as s on o.settlement_id = s.settlement_id
{% if is_incremental() %}
    where {{ rebuild_filter('o.settlement_id') }}
{% endif %}
