-- INV-06: every execution belongs to exactly one settlement obligation, and a
-- settled obligation's quantity and amount equal the sums over its executions.
-- The amount is recomputed here only to check Python's posting (ADR 007).
-- Returns the obligations that break it.
with executions as (
    select
        execution_id,
        settlement_id,
        quantity,
        round(quantity * price * multiplier, 2) as gross_amount
    from {{ ref('fct_executions') }}
),

listed as (
    select
        s.settlement_id,
        jsonb_array_elements_text(s.execution_ids) as execution_id
    from {{ ref('stg_brokerage__trade_settled') }} as s
),

membership_breaks as (
    -- listed by the settlement but not pointing at it, or pointing at it but not listed
    select coalesce(l.settlement_id, e.settlement_id) as settlement_id
    from listed as l
    full outer join executions as e
        on l.execution_id = e.execution_id and l.settlement_id = e.settlement_id
    inner join {{ ref('fct_settlements') }} as f
        on coalesce(l.settlement_id, e.settlement_id) = f.settlement_id
    where f.settlement_status = 'SETTLED' and (l.execution_id is null or e.execution_id is null)
),

sums as (
    select
        settlement_id,
        sum(quantity) as quantity,
        sum(gross_amount) as amount
    from executions
    group by settlement_id
),

amount_breaks as (
    select f.settlement_id
    from {{ ref('fct_settlements') }} as f
    inner join sums as s on f.settlement_id = s.settlement_id
    where
        f.settlement_status = 'SETTLED'
        and (f.settled_quantity != s.quantity or f.settled_amount != s.amount)
)

select settlement_id from membership_breaks
union
select settlement_id from amount_breaks
