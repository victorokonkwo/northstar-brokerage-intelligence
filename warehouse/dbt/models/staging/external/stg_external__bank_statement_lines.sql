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
    payload ->> 'reference' as reference,
    (payload ->> 'value_date')::date as value_date,
    payload ->> 'currency' as currency,
    (payload ->> 'amount')::numeric(18, 2) as amount,
    payload ->> 'description' as description
from source
