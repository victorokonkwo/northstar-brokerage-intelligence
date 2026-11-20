-- One row per dividend_paid event (one dividend's payment on its pay date,
-- roadmap 5C-a). Rename and cast only. Each account's amount is read from the
-- ledger (fct_dividend_payments), not from the payload's allocations.
with source as (
    select * from {{ source('brokerage', 'brokerage_events') }}
    where event_type = 'dividend_paid'
)

select
    event_id,
    event_timestamp,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'dividend_id') }} as dividend_id,
    {{ json_text('payload', 'instrument_id') }} as instrument_id,
    {{ json_text('payload', 'currency') }} as currency,
    ({{ json_text('payload', 'amount_per_share') }})::numeric(18, 8) as amount_per_share,
    ({{ json_text('payload', 'record_date') }})::date as record_date,
    ({{ json_text('payload', 'pay_date') }})::date as pay_date,
    ({{ json_text('payload', 'total_amount') }})::numeric(18, 2) as total_amount
from source
