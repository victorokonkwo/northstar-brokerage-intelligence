-- One row per bank statement line. The amount is signed from the bank's view (credit +, debit -).
-- Rename and cast only.
with source as (
    select * from {{ source('external', 'external_records') }}
    where record_set = 'bank_statement_lines'
)

select
    record_id,
    event_date,
    schema_version,
    _ingestion_run_id,
    _ingested_at,
    {{ json_text('payload', 'reference') }} as reference,
    ({{ json_text('payload', 'value_date') }})::date as value_date,
    {{ json_text('payload', 'currency') }} as currency,
    ({{ json_text('payload', 'amount') }})::numeric(18, 2) as amount,
    {{ json_text('payload', 'description') }} as description
from source
