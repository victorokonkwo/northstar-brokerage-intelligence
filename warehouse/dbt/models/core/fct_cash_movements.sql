-- One row per client cash movement: a deposit, a withdrawal, or an FX
-- conversion. Money in and money out are separate columns, each with its
-- currency; a conversion has both (out = sold, in = bought).
with deposits as (
    select
        cash_movement_id,
        account_id,
        'DEPOSIT'::{{ string_type() }} as movement_type,
        event_timestamp,
        amount as amount_in,
        currency as currency_in,
        null::numeric(18, 2) as amount_out,
        null::{{ string_type() }} as currency_out,
        null::numeric(18, 10) as reference_rate,
        null::numeric(18, 10) as client_rate,
        null::date as rate_date,
        null::{{ string_type() }} as fx_policy_version,
        event_id,
        schema_version,
        _ingestion_run_id,
        _ingested_at
    from {{ ref('stg_brokerage__cash_deposited') }}
),

withdrawals as (
    select
        cash_movement_id,
        account_id,
        'WITHDRAWAL'::{{ string_type() }} as movement_type,
        event_timestamp,
        null::numeric(18, 2) as amount_in,
        null::{{ string_type() }} as currency_in,
        amount as amount_out,
        currency as currency_out,
        null::numeric(18, 10) as reference_rate,
        null::numeric(18, 10) as client_rate,
        null::date as rate_date,
        null::{{ string_type() }} as fx_policy_version,
        event_id,
        schema_version,
        _ingestion_run_id,
        _ingested_at
    from {{ ref('stg_brokerage__cash_withdrawn') }}
),

conversions as (
    select
        fx_conversion_id as cash_movement_id,
        account_id,
        'FX_CONVERSION'::{{ string_type() }} as movement_type,
        event_timestamp,
        buy_amount as amount_in,
        buy_currency as currency_in,
        sell_amount as amount_out,
        sell_currency as currency_out,
        reference_rate,
        client_rate,
        rate_date,
        fx_policy_version,
        event_id,
        schema_version,
        _ingestion_run_id,
        _ingested_at
    from {{ ref('stg_brokerage__fx_converted') }}
),

movements as (
    select * from deposits
    union all
    select * from withdrawals
    union all
    select * from conversions
)

select
    cash_movement_id,
    account_id,
    movement_type,
    event_timestamp,
    (event_timestamp at time zone 'America/Toronto')::date as business_date,
    amount_in,
    currency_in,
    amount_out,
    currency_out,
    reference_rate,
    client_rate,
    rate_date,
    fx_policy_version,
    event_id as _source_event_id,
    _ingestion_run_id,
    _ingested_at,
    schema_version as _schema_version,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from movements
