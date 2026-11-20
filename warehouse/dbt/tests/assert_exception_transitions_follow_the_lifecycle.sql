-- FR-M2-05, FR-M2-06: every stored transition obeys the rules that
-- recon.exception_transitions' CHECK and UNIQUE constraints enforce on Postgres
-- (northstar.infrastructure.recon_store). Redshift has no CHECK constraints and
-- does not enforce UNIQUE, so on the cloud warehouse this test is the database-side
-- control, next to the domain's check() before every write (owner decision 2026-11-20).
-- Returns each breaking transition with the rule it breaks (broken_rule).
with transitions as (
    select * from {{ ref('stg_recon__exception_transitions') }}
),

repeated as (
    select
        exception_id,
        sequence
    from transitions
    group by exception_id, sequence
    having count(*) > 1
),

breaks as (
    select
        transition_id,
        'sequence must be 1 or more' as broken_rule
    from transitions
    where sequence < 1

    union all
    select
        transition_id,
        'unknown status' as broken_rule
    from transitions
    where to_status not in ('OPEN', 'INVESTIGATING', 'ESCALATED', 'RESOLVED')

    union all
    select
        transition_id,
        'actor must be rule:<id>@<version> or user:<name>' as broken_rule
    from transitions
    where actor not like 'rule:%' and actor not like 'user:%'

    union all
    select
        transition_id,
        'every transition needs a reason' as broken_rule
    from transitions
    where reason is null or length(trim(reason)) = 0

    union all
    select
        transition_id,
        'unknown resolution type' as broken_rule
    from transitions
    where resolution_type not in ('AUTO_RULE', 'MANUAL_OVERRIDE', 'SOURCE_CORRECTED')

    union all
    select
        transition_id,
        'RESOLVED needs a resolution type, and only RESOLVED has one' as broken_rule
    from transitions
    where (to_status = 'RESOLVED') <> (resolution_type is not null)

    union all
    select
        transition_id,
        'a manual override needs a person' as broken_rule
    from transitions
    where resolution_type = 'MANUAL_OVERRIDE' and actor not like 'user:%'

    union all
    select
        transition_id,
        'an automatic resolution needs a rule' as broken_rule
    from transitions
    where resolution_type in ('AUTO_RULE', 'SOURCE_CORRECTED') and actor not like 'rule:%'

    union all
    select
        t.transition_id,
        'sequence repeats within an exception' as broken_rule
    from transitions as t
    inner join repeated as d
        on t.exception_id = d.exception_id and t.sequence = d.sequence
)

select * from breaks
