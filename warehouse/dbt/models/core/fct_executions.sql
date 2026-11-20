-- Canonical executions (domain model §3). One row per execution (fill).
-- The gross amount is not recomputed here: it is posted by Python and lives
-- in fct_journal_lines (ADR 007).
with executions as (
    select
        execution_id,
        order_id,
        account_id,
        instrument_id,
        market,
        asset_class,
        side,
        quantity,
        price,
        multiplier,
        currency,
        commission,
        clearing_fee,
        trade_date,
        settlement_date,
        settlement_id,
        fee_schedule_version,
        settlement_rules_version,
        calendar_version,
        event_timestamp,
        event_id,
        schema_version,
        _ingestion_run_id,
        _ingested_at
    from {{ ref('stg_brokerage__trade_executed') }}
)

select
    execution_id,
    order_id,
    account_id,
    instrument_id,
    market,
    asset_class,
    side,
    quantity,
    price,
    multiplier,
    currency,
    commission,
    clearing_fee,
    event_timestamp as executed_at,
    trade_date,
    settlement_date,
    settlement_id,
    fee_schedule_version,
    settlement_rules_version,
    calendar_version,
    event_id as _source_event_id,
    _ingestion_run_id,
    _ingested_at,
    schema_version as _schema_version,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from executions
