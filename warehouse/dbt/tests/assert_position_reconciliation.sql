-- INV-03: each daily position equals the cumulative quantity moved up to that
-- date, recomputed here from the facts: executions, plus exercises (the option
-- out, the underlying in for a call or out for a put) and expiries (the option
-- out; roadmap 5C-b). Never negative (long only). Returns the positions that
-- break it.
with moves as (
    select
        account_id,
        instrument_id,
        trade_date as on_date,
        case when side = 'BUY' then quantity else -quantity end as quantity
    from {{ ref('fct_executions') }}
    union all
    select
        account_id,
        option_instrument_id as instrument_id,
        exercise_date as on_date,
        -contracts as quantity
    from {{ ref('stg_brokerage__option_exercised') }}
    union all
    select
        account_id,
        underlying_instrument_id as instrument_id,
        exercise_date as on_date,
        case
            when option_right = 'CALL' then underlying_quantity
            else -underlying_quantity
        end as quantity
    from {{ ref('stg_brokerage__option_exercised') }}
    union all
    select
        account_id,
        instrument_id,
        expiry_date as on_date,
        -contracts as quantity
    from {{ ref('stg_brokerage__option_expired') }}
),

recomputed as (
    select
        p.account_id,
        p.instrument_id,
        p.business_date,
        p.trade_date_quantity,
        p.settled_quantity,
        sum(m.quantity) as expected
    from {{ ref('fct_positions_daily') }} as p
    inner join moves as m
        on
            p.account_id = m.account_id
            and p.instrument_id = m.instrument_id
            and p.business_date >= m.on_date
    group by
        p.account_id, p.instrument_id, p.business_date, p.trade_date_quantity, p.settled_quantity
)

select *
from recomputed
where trade_date_quantity != expected or trade_date_quantity < 0 or settled_quantity < 0
