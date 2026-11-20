-- One row per posted journal line. Rename and cast only.
with source as (
    select * from {{ source('ledger', 'journal_lines') }}
)

select
    journal_line_id,
    journal_batch_id,
    line_number,
    source_event_id,
    source_event_type,
    posting_rule_id,
    posting_rule_version,
    chart_version,
    ledger_calendar_version,
    business_date,
    posting_date,
    gl_account::{{ string_type() }} as gl_account,
    client_account_id,
    entry_side,
    amount,
    currency::{{ string_type() }} as currency,
    _ingestion_run_id,
    _ingested_at
from source
