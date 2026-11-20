-- One row per order. Filled quantity is the sum of its executions; a
-- cancellation closes the unfilled remainder.
-- Status: FILLED (fully executed), CANCELLED (remainder cancelled, with or
-- without fills), PARTIALLY_FILLED (some fills, remainder still open), OPEN.
with orders as (
    select
        order_id,
        account_id,
        instrument_id,
        market,
        side,
        order_type,
        quantity,
        limit_price,
        currency,
        event_timestamp as placed_at,
        event_id,
        schema_version,
        _ingestion_run_id,
        _ingested_at
    from {{ ref('stg_brokerage__order_placed') }}
),

fills as (
    select
        order_id,
        sum(quantity) as filled_quantity,
        count(*) as execution_count
    from {{ ref('stg_brokerage__trade_executed') }}
    group by order_id
),

cancellations as (
    select
        order_id,
        cancelled_quantity,
        event_timestamp as cancelled_at
    from {{ ref('stg_brokerage__order_cancelled') }}
)

select
    o.order_id,
    o.account_id,
    o.instrument_id,
    o.market,
    o.side,
    o.order_type,
    o.quantity,
    o.limit_price,
    o.currency,
    o.placed_at,
    coalesce(f.filled_quantity, 0)::numeric(18, 8) as filled_quantity,
    coalesce(c.cancelled_quantity, 0)::numeric(18, 8) as cancelled_quantity,
    coalesce(f.execution_count, 0)::integer as execution_count,
    c.cancelled_at,
    case
        when coalesce(f.filled_quantity, 0) = o.quantity then 'FILLED'
        when c.order_id is not null then 'CANCELLED'
        when coalesce(f.filled_quantity, 0) > 0 then 'PARTIALLY_FILLED'
        else 'OPEN'
    end as order_status,
    o.event_id as _source_event_id,
    o._ingestion_run_id,
    o._ingested_at,
    o.schema_version as _schema_version,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from orders as o
left join fills as f on o.order_id = f.order_id
left join cancellations as c on o.order_id = c.order_id
