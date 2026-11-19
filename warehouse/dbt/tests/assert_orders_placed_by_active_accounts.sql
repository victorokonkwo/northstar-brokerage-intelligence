-- FR-M1-05 point-in-time join: each order matches exactly one dim_account
-- version valid when it was placed, and that version was ACTIVE (a restricted
-- account cannot trade). Returns the orders that break it.
with matches as (
    select
        o.order_id,
        count(a.account_sk) as versions,
        bool_and(a.status = 'ACTIVE') as all_active
    from {{ ref('fct_orders') }} as o
    left join {{ ref('dim_account') }} as a
        on
            o.account_id = a.account_id
            and o.placed_at >= a.valid_from
            and (a.valid_to is null or o.placed_at < a.valid_to)
    group by o.order_id
)

select
    order_id,
    versions,
    all_active
from matches
where versions != 1 or not all_active
