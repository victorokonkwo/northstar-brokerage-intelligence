-- KPI-05: obligations due on a date that did not settle on time / all due,
-- per contractual settlement date x currency x asset class, by count and value.
-- The ledger settles every obligation on its contractual date, so a fail is
-- what the depository reports: a SETTLEMENT_LATE or a missing confirmation.
with obligations as (
    select
        s.settlement_id,
        s.contractual_settlement_date as business_date,
        s.currency,
        i.asset_class,
        s.settled_amount
    from {{ ref('fct_settlements') }} as s
    inner join {{ ref('dim_instrument') }} as i
        on s.instrument_id = i.instrument_id and i.is_current
    where s.settlement_status = 'SETTLED'
),

failed as (
    select distinct reference as settlement_id
    from {{ ref('fct_reconciliation_exceptions') }}
    where
        reconciliation = 'settlements'
        and exception_type in ('SETTLEMENT_LATE', 'MISSING_EXTERNAL')
),

daily as (
    select
        o.business_date,
        o.currency,
        o.asset_class,
        count(*) as obligations_due,
        count(f.settlement_id) as failed_count,
        sum(o.settled_amount) as value_due,
        coalesce(sum(case when f.settlement_id is not null then o.settled_amount end), 0)
            as value_failed
    from obligations as o
    left join failed as f on o.settlement_id = f.settlement_id
    group by o.business_date, o.currency, o.asset_class
)

select
    business_date,
    currency,
    asset_class,
    obligations_due::integer as obligations_due,
    failed_count::integer as failed_count,
    {{ count_ratio_half_even('failed_count', 'obligations_due', 6) }} as fail_rate,
    value_due::numeric(18, 2) as value_due,
    value_failed::numeric(18, 2) as value_failed,
    {{ round_half_even('value_failed / nullif(value_due, 0)', 6) }} as value_fail_rate,
    '{{ invocation_id }}'::{{ string_type() }} as _dbt_invocation_id
from daily
