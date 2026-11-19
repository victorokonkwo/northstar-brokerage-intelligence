-- Account history as SCD Type 2, built from the event log (FR-M1-05).
-- One row per account version: the opening, then each account_updated event.
-- valid_from is the event timestamp (inclusive) and valid_to the next version's
-- (exclusive; null = current), so a point-in-time join uses event time, and a
-- replay or backfill rebuilds exactly the same versions.
with created as (
    select
        account_id,
        event_timestamp as valid_from,
        account_type,
        base_currency,
        'ACTIVE'::text as status,
        'ACCOUNT_OPENED'::text as change_reason,
        event_id,
        schema_version,
        _ingestion_run_id,
        _ingested_at
    from {{ ref('stg_brokerage__account_created') }}
),

updated as (
    select
        u.account_id,
        u.event_timestamp as valid_from,
        u.account_type,
        c.base_currency,
        u.status,
        u.reason as change_reason,
        u.event_id,
        u.schema_version,
        u._ingestion_run_id,
        u._ingested_at
    from {{ ref('stg_brokerage__account_updated') }} as u
    inner join created as c on u.account_id = c.account_id
),

versions as (
    select * from created
    union all
    select * from updated
),

final as (
    select
        {{ surrogate_key(['account_id', 'valid_from']) }} as account_sk,
        account_id,
        account_type,
        base_currency,
        status,
        change_reason,
        account_type = 'MARGIN' as is_margin_eligible,
        min(valid_from) over (partition by account_id) as opened_at,
        valid_from,
        lead(valid_from) over (partition by account_id order by valid_from) as valid_to,
        event_id,
        schema_version,
        _ingestion_run_id,
        _ingested_at
    from versions
)

select
    account_sk,
    account_id,
    account_type,
    base_currency,
    status,
    change_reason,
    is_margin_eligible,
    opened_at,
    valid_from,
    valid_to,
    valid_to is null as is_current,
    event_id as _source_event_id,
    _ingestion_run_id,
    _ingested_at,
    schema_version as _schema_version,
    '{{ invocation_id }}'::text as _dbt_invocation_id
from final
