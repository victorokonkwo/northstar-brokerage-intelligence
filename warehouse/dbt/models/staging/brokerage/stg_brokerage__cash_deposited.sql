-- One row per cash_deposited event. Rename and cast only.
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'cash_deposited'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    payload ->> 'cash_movement_id' as cash_movement_id,
    payload ->> 'account_id' as account_id,
    (payload ->> 'amount')::numeric(18, 2) as amount,
    payload ->> 'currency' as currency
from source
