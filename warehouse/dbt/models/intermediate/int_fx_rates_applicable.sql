-- The USD/CAD rate that applies on each calendar date, under FX fallback
-- policy v1 (domain model §6): the most recent rate published on or before
-- the date. Each row records the rate's own date and policy version (INV-07),
-- so a fallback is visible. Dates run from the first published rate to the
-- last reporting date; a date before the first rate has no rate, and a
-- translation there fails its test rather than guessing one.
with rates as (
    select
        rate_date,
        rate,
        fx_policy_version
    from {{ ref('fct_fx_rates_daily') }}
    where base_currency = 'USD' and quote_currency = 'CAD'
),

dates as (
    select c.calendar_date
    from {{ ref('dim_calendar') }} as c
    where
        c.market = 'XTSE'
        and c.calendar_date between (select min(r.rate_date) from rates as r)
        and (select max(rd.business_date) from {{ ref('int_reporting_dates') }} as rd)
),

applicable as (
    select
        d.calendar_date,
        max(r.rate_date) as fx_rate_date
    from dates as d
    inner join rates as r on d.calendar_date >= r.rate_date
    group by d.calendar_date
)

select
    a.calendar_date,
    r.rate,
    a.fx_rate_date,
    r.fx_policy_version,
    a.fx_rate_date < a.calendar_date as is_fallback
from applicable as a
inner join rates as r on a.fx_rate_date = r.rate_date
