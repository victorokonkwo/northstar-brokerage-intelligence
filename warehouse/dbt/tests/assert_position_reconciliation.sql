-- INV-03: each daily position equals the cumulative executed quantity up to
-- that date, recomputed here from fct_executions, and is never negative (long
-- only). Returns the positions that break it.
with recomputed as (
    select
        p.account_id,
        p.instrument_id,
        p.business_date,
        p.trade_date_quantity,
        p.settled_quantity,
        sum(case when e.side = 'BUY' then e.quantity else -e.quantity end) as expected
    from {{ ref('fct_positions_daily') }} as p
    inner join {{ ref('fct_executions') }} as e
        on
            p.account_id = e.account_id
            and p.instrument_id = e.instrument_id
            and p.business_date >= e.trade_date
    group by
        p.account_id, p.instrument_id, p.business_date, p.trade_date_quantity, p.settled_quantity
)

select *
from recomputed
where trade_date_quantity != expected or trade_date_quantity < 0 or settled_quantity < 0
