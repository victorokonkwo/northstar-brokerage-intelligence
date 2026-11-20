{{ config(
    materialized='incremental',
    incremental_strategy='rebuild',
    on_schema_change='fail',
    post_hook="{{ record_build() }}",
    meta={'rebuild': {'new': true}},
) }}

-- One row per raw record the incremental models read, recorded once, in the
-- build that first saw it (macros/incremental.sql). effective_date is the
-- first business date the record can change; null means it can change any
-- date. The keys say which accounts, orders, settlements, and instruments it
-- touches. The records themselves are append-only (W3.3), so a record never
-- needs recording twice.
with arrivals as (
    select
        'stg_brokerage__account_created' as source_model,
        event_id as record_id,
        (event_timestamp at time zone 'America/Toronto')::date as effective_date,
        account_id,
        null::{{ string_type() }} as order_id,
        null::{{ string_type() }} as settlement_id,
        null::{{ string_type() }} as instrument_id
    from {{ ref('stg_brokerage__account_created') }}

    union all

    select
        'stg_brokerage__account_updated' as source_model,
        event_id as record_id,
        (event_timestamp at time zone 'America/Toronto')::date as effective_date,
        account_id,
        null as order_id,
        null as settlement_id,
        null as instrument_id
    from {{ ref('stg_brokerage__account_updated') }}

    union all

    select
        'stg_brokerage__cash_deposited' as source_model,
        event_id as record_id,
        (event_timestamp at time zone 'America/Toronto')::date as effective_date,
        account_id,
        null as order_id,
        null as settlement_id,
        null as instrument_id
    from {{ ref('stg_brokerage__cash_deposited') }}

    union all

    select
        'stg_brokerage__cash_withdrawn' as source_model,
        event_id as record_id,
        (event_timestamp at time zone 'America/Toronto')::date as effective_date,
        account_id,
        null as order_id,
        null as settlement_id,
        null as instrument_id
    from {{ ref('stg_brokerage__cash_withdrawn') }}

    union all

    select
        'stg_brokerage__fx_converted' as source_model,
        event_id as record_id,
        (event_timestamp at time zone 'America/Toronto')::date as effective_date,
        account_id,
        null as order_id,
        null as settlement_id,
        null as instrument_id
    from {{ ref('stg_brokerage__fx_converted') }}

    union all

    -- A rate applies from its rate date until a newer one (int_fx_rates_applicable).
    select
        'stg_brokerage__fx_rate_published' as source_model,
        event_id as record_id,
        rate_date as effective_date,
        null as account_id,
        null as order_id,
        null as settlement_id,
        null as instrument_id
    from {{ ref('stg_brokerage__fx_rate_published') }}

    union all

    -- Instruments join every reporting date (dim_instrument.is_current), so a
    -- listing can change any date.
    select
        'stg_brokerage__instrument_listed' as source_model,
        event_id as record_id,
        null as effective_date,
        null as account_id,
        null as order_id,
        null as settlement_id,
        instrument_id
    from {{ ref('stg_brokerage__instrument_listed') }}

    union all

    select
        'stg_brokerage__order_placed' as source_model,
        event_id as record_id,
        (event_timestamp at time zone 'America/Toronto')::date as effective_date,
        account_id,
        order_id,
        null as settlement_id,
        instrument_id
    from {{ ref('stg_brokerage__order_placed') }}

    union all

    select
        'stg_brokerage__order_cancelled' as source_model,
        event_id as record_id,
        (event_timestamp at time zone 'America/Toronto')::date as effective_date,
        account_id,
        order_id,
        null as settlement_id,
        null as instrument_id
    from {{ ref('stg_brokerage__order_cancelled') }}

    union all

    -- Trade-date quantities and execution amounts count from the trade date.
    select
        'stg_brokerage__trade_executed' as source_model,
        event_id as record_id,
        trade_date as effective_date,
        account_id,
        order_id,
        settlement_id,
        instrument_id
    from {{ ref('stg_brokerage__trade_executed') }}

    union all

    -- Settled quantities count from the settlement date.
    select
        'stg_brokerage__trade_settled' as source_model,
        event_id as record_id,
        settlement_date as effective_date,
        account_id,
        order_id,
        settlement_id,
        instrument_id
    from {{ ref('stg_brokerage__trade_settled') }}

    union all

    -- An exercise moves positions from its exercise date and opens its obligation
    -- (roadmap 5C-b); its settlement and an expiry move them from their dates.
    select
        'stg_brokerage__option_exercised' as source_model,
        event_id as record_id,
        exercise_date as effective_date,
        account_id,
        null as order_id,
        settlement_id,
        option_instrument_id as instrument_id
    from {{ ref('stg_brokerage__option_exercised') }}

    union all

    select
        'stg_brokerage__exercise_settled' as source_model,
        event_id as record_id,
        settlement_date as effective_date,
        account_id,
        null as order_id,
        settlement_id,
        instrument_id
    from {{ ref('stg_brokerage__exercise_settled') }}

    union all

    select
        'stg_brokerage__option_expired' as source_model,
        event_id as record_id,
        expiry_date as effective_date,
        account_id,
        null as order_id,
        null as settlement_id,
        instrument_id
    from {{ ref('stg_brokerage__option_expired') }}

    union all

    -- A close is the latest price from its price date until a newer one.
    select
        'stg_brokerage__price_updated' as source_model,
        event_id as record_id,
        price_date as effective_date,
        null as account_id,
        null as order_id,
        null as settlement_id,
        instrument_id
    from {{ ref('stg_brokerage__price_updated') }}

    union all

    -- A line counts from its business date (balances, KPI-01) or its posting
    -- date (daily marts), whichever is earlier, and it can extend the
    -- reporting dates (int_reporting_dates).
    select
        'stg_ledger__journal_lines' as source_model,
        cast(journal_line_id as {{ string_type() }}) as record_id,
        least(business_date, posting_date) as effective_date,
        client_account_id as account_id,
        null as order_id,
        null as settlement_id,
        null as instrument_id
    from {{ ref('stg_ledger__journal_lines') }}

    union all

    -- A calendar day can change reporting dates and expected price dates on or after it.
    select
        'stg_reference__market_calendar_days' as source_model,
        calendar_day_id as record_id,
        calendar_date as effective_date,
        null as account_id,
        null as order_id,
        null as settlement_id,
        null as instrument_id
    from {{ ref('stg_reference__market_calendar_days') }}
)

select
    a.source_model::{{ string_type() }} as source_model,
    a.record_id::{{ string_type() }} as record_id,
    a.effective_date::date as effective_date,
    a.account_id::{{ string_type() }} as account_id,
    a.order_id::{{ string_type() }} as order_id,
    a.settlement_id::{{ string_type() }} as settlement_id,
    a.instrument_id::{{ string_type() }} as instrument_id,
    {{ current_build_seq() }} as _build_seq
from arrivals as a
{% if is_incremental() %}
    where
        {{ rebuild_filter() }}
        or not exists (
            select 1 from {{ this }} as t
            where t.source_model = a.source_model and t.record_id = a.record_id
        )
{% endif %}
