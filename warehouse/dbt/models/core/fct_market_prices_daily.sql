-- One row per instrument and reporting date that has a close on or before it.
-- The price is the latest close on or before the date. It is stale (INV-08)
-- when the instrument's market traded after that close, on or before the
-- date, and no newer close arrived. A close carried over a day the market was
-- shut is current, not stale. A date with no close at all gets no row here;
-- positions then show the price as missing rather than guessing one.
with closes as (
    select
        instrument_id,
        price_date,
        close_price,
        currency,
        price_source,
        event_id,
        schema_version,
        _ingestion_run_id,
        _ingested_at
    from {{ ref('stg_brokerage__price_updated') }}
),

pairs as (
    select
        i.instrument_id,
        i.market,
        d.business_date
    from {{ ref('dim_instrument') }} as i
    cross join {{ ref('int_reporting_dates') }} as d
    where i.is_current
),

latest as (
    select
        p.instrument_id,
        p.market,
        p.business_date,
        max(c.price_date) as price_date
    from pairs as p
    inner join closes as c
        on p.instrument_id = c.instrument_id and p.business_date >= c.price_date
    group by p.instrument_id, p.market, p.business_date
),

expected as (
    select
        l.instrument_id,
        l.business_date,
        max(cal.calendar_date) as expected_price_date
    from latest as l
    inner join {{ ref('dim_calendar') }} as cal
        on
            l.market = cal.market
            and cal.is_trading_day
            and l.business_date >= cal.calendar_date
    group by l.instrument_id, l.business_date
)

select
    l.instrument_id,
    l.business_date,
    l.price_date,
    c.close_price,
    c.currency,
    c.price_source,
    e.expected_price_date,
    coalesce(l.price_date < e.expected_price_date, true) as is_stale,
    c.event_id as _source_event_id,
    c._ingestion_run_id,
    c._ingested_at,
    c.schema_version as _schema_version,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from latest as l
inner join closes as c
    on l.instrument_id = c.instrument_id and l.price_date = c.price_date
left join expected as e
    on l.instrument_id = e.instrument_id and l.business_date = e.business_date
