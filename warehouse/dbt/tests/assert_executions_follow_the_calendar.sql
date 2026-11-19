-- Domain model §5: every execution is on a trading day of its market, and its
-- settlement date is a settlement day of that market, per dim_calendar.
-- Returns the executions that break it (or whose dates the calendar doesn't cover).
select
    e.execution_id,
    e.market,
    e.trade_date,
    e.settlement_date
from {{ ref('fct_executions') }} as e
left join {{ ref('dim_calendar') }} as t
    on e.market = t.market and e.trade_date = t.calendar_date
left join {{ ref('dim_calendar') }} as s
    on e.market = s.market and e.settlement_date = s.calendar_date
where
    t.calendar_day_id is null
    or s.calendar_day_id is null
    or not t.is_trading_day
    or not s.is_settlement_day
    or e.settlement_date <= e.trade_date
