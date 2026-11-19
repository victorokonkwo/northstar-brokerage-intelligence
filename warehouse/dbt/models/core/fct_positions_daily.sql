-- One row per account x instrument x reporting date with a non-zero
-- trade-date or settled quantity. Trade-date quantity is the sum of executed
-- quantities with trade_date on or before the date; settled quantity is the
-- sum of settled quantities with settlement_date on or before it (long only).
-- Market value is trade-date quantity x close x multiplier, rounded half-even
-- once (ADR 004). It is null when no close exists (INV-08: never valued with a
-- missing price), and the stale flag is carried from the price. USD values are
-- translated to CAD at the applicable reference rate (INV-07).
with executed as (
    select
        account_id,
        instrument_id,
        trade_date as on_date,
        sum(case when side = 'BUY' then quantity else -quantity end) as quantity
    from {{ ref('stg_brokerage__trade_executed') }}
    group by account_id, instrument_id, trade_date
),

settled as (
    select
        account_id,
        instrument_id,
        settlement_date as on_date,
        sum(case when side = 'BUY' then quantity else -quantity end) as quantity
    from {{ ref('stg_brokerage__trade_settled') }}
    group by account_id, instrument_id, settlement_date
),

holdings as (
    select
        account_id,
        instrument_id,
        min(on_date) as first_date
    from executed
    group by account_id, instrument_id
),

grid as (
    select
        h.account_id,
        h.instrument_id,
        d.business_date
    from holdings as h
    inner join {{ ref('int_reporting_dates') }} as d on h.first_date <= d.business_date
),

traded_to_date as (
    select
        g.account_id,
        g.instrument_id,
        g.business_date,
        sum(x.quantity) as trade_date_quantity
    from grid as g
    inner join executed as x
        on
            g.account_id = x.account_id
            and g.instrument_id = x.instrument_id
            and g.business_date >= x.on_date
    group by g.account_id, g.instrument_id, g.business_date
),

settled_to_date as (
    select
        g.account_id,
        g.instrument_id,
        g.business_date,
        sum(s.quantity) as settled_quantity
    from grid as g
    inner join settled as s
        on
            g.account_id = s.account_id
            and g.instrument_id = s.instrument_id
            and g.business_date >= s.on_date
    group by g.account_id, g.instrument_id, g.business_date
),

quantities as (
    select
        t.account_id,
        t.instrument_id,
        t.business_date,
        t.trade_date_quantity,
        coalesce(s.settled_quantity, 0) as settled_quantity
    from traded_to_date as t
    left join settled_to_date as s
        on
            t.account_id = s.account_id
            and t.instrument_id = s.instrument_id
            and t.business_date = s.business_date
),

valued as (
    select
        q.account_id,
        q.instrument_id,
        q.business_date,
        q.trade_date_quantity,
        q.settled_quantity,
        i.currency,
        i.multiplier,
        p.close_price,
        p.price_date,
        p._source_event_id as price_event_id,
        p.close_price is null as is_price_missing,
        coalesce(p.is_stale, false) as is_price_stale,
        q.trade_date_quantity * p.close_price * i.multiplier as unrounded_value,
        case when i.currency = 'USD' then fx.rate end as fx_rate,
        case when i.currency = 'USD' then fx.fx_rate_date end as fx_rate_date,
        case when i.currency = 'USD' then fx.fx_policy_version end as fx_policy_version
    from quantities as q
    inner join {{ ref('dim_instrument') }} as i
        on q.instrument_id = i.instrument_id and i.is_current
    left join {{ ref('fct_market_prices_daily') }} as p
        on q.instrument_id = p.instrument_id and q.business_date = p.business_date
    left join {{ ref('int_fx_rates_applicable') }} as fx
        on q.business_date = fx.calendar_date
    where q.trade_date_quantity != 0 or q.settled_quantity != 0
)

select
    account_id,
    instrument_id,
    business_date,
    trade_date_quantity::numeric(18, 8) as trade_date_quantity,
    settled_quantity::numeric(18, 8) as settled_quantity,
    currency,
    close_price,
    price_date,
    is_price_missing,
    is_price_stale,
    {{ round_half_even('unrounded_value', 2) }} as market_value,
    case
        when currency = 'CAD' then {{ round_half_even('unrounded_value', 2) }}
        else {{ round_half_even('unrounded_value * fx_rate', 2) }}
    end as market_value_cad,
    fx_rate,
    fx_rate_date,
    fx_policy_version,
    price_event_id as _price_event_id,
    '{{ invocation_id }}'::text as _dbt_invocation_id
from valued
