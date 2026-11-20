-- Canonical journal lines (domain model §3). One row per journal line.
-- Posting is owned by Python (ADR 007); this model only reshapes the ledger
-- into debit and credit columns for set-based checks such as KPI-01.
with journal_lines as (
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
        gl_account,
        client_account_id,
        entry_side,
        amount,
        currency,
        _ingestion_run_id,
        _ingested_at
    from {{ ref('stg_ledger__journal_lines') }}
)

select
    journal_line_id,
    journal_batch_id,
    line_number,
    gl_account,
    client_account_id,
    case when entry_side = 'DEBIT' then amount else 0 end::numeric(18, 2) as debit_amount,
    case when entry_side = 'CREDIT' then amount else 0 end::numeric(18, 2) as credit_amount,
    currency,
    business_date,
    posting_date,
    posting_rule_id,
    posting_rule_version,
    chart_version,
    ledger_calendar_version,
    source_event_type,
    source_event_id as _source_event_id,
    _ingestion_run_id,
    _ingested_at,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from journal_lines
