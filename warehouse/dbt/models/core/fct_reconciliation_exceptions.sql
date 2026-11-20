{{ config(tags=['controls']) }}

-- One row per reconciliation exception with its current lifecycle state: the
-- latest transition gives the status; resolution and escalation come from the
-- trail. "Past SLA" is the recorded sla_breach escalation (Python owns the
-- business-day age, ADR 007), not recomputed here.
with exceptions as (
    select
        exception_id,
        reconciliation,
        exception_type,
        severity,
        reference,
        business_date,
        impact_amount,
        impact_currency,
        impact_basis,
        impact_cad,
        fx_rate_date,
        fx_policy_version,
        external_record_ids,
        policy_version,
        run_id
    from {{ ref('stg_recon__exceptions') }}
),

transitions as (
    select
        exception_id,
        sequence,
        to_status,
        actor,
        resolution_type,
        changed_at,
        row_number() over (partition by exception_id order by sequence desc) as recency
    from {{ ref('stg_recon__exception_transitions') }}
),

trail as (
    select
        exception_id,
        min(changed_at) as opened_at,
        max(case when to_status = 'RESOLVED' then changed_at end) as resolved_at,
        bool_or(to_status = 'ESCALATED') as was_escalated,
        count(*) as transition_count
    from transitions
    group by exception_id
)

select
    e.exception_id::text as exception_id,
    e.reconciliation,
    e.exception_type,
    e.severity,
    e.reference,
    e.business_date,
    e.impact_amount,
    e.impact_currency::text as impact_currency,
    e.impact_basis,
    e.impact_cad,
    e.fx_rate_date,
    e.fx_policy_version,
    e.external_record_ids,
    l.to_status as status,
    l.resolution_type,
    l.actor as last_actor,
    t.opened_at,
    t.resolved_at,
    t.was_escalated,
    t.transition_count::integer as transition_count,
    e.policy_version,
    e.run_id as _detected_run_id,
    '{{ invocation_id }}'::text as _dbt_invocation_id
from exceptions as e
inner join transitions as l on e.exception_id = l.exception_id and l.recency = 1
inner join trail as t on e.exception_id = t.exception_id
