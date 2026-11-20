-- SCD2 integrity for dim_account: each version ends exactly where the next
-- begins, exactly one version per account is current, and versions are
-- ordered by valid_from. Returns the accounts that break it.
with ordered as (
    select
        account_id,
        valid_from,
        valid_to,
        is_current,
        lead(valid_from) over (partition by account_id order by valid_from) as next_from
    from {{ ref('dim_account') }}
),

breaks as (
    select account_id
    from ordered
    where
        (next_from is null and valid_to is not null)
        or (next_from is not null and {{ distinct_from('valid_to', 'next_from') }})
        or valid_to <= valid_from
    union all
    select account_id
    from ordered
    group by account_id
    having sum(case when is_current then 1 else 0 end) <> 1
)

select distinct account_id from breaks
