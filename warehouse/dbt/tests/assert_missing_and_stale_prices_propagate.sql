-- INV-08: a position without a price has no market value, and the account
-- total is null with the flag set; stale and missing flags in the mart equal
-- the flags of the positions under it. Returns the rows that break it.
with by_account as (
    select
        account_id,
        business_date,
        bool_or(is_price_missing) as any_missing,
        bool_or(is_price_stale) as any_stale
    from {{ ref('fct_positions_daily') }}
    group by account_id, business_date
)

select
    'position' as check_level,
    p.account_id,
    p.business_date
from {{ ref('fct_positions_daily') }} as p
where p.is_price_missing and (p.market_value is not null or p.market_value_cad is not null)
union all
select
    'account' as check_level,
    m.account_id,
    m.business_date
from {{ ref('mart_daily_positions') }} as m
inner join by_account as b
    on m.account_id = b.account_id and m.business_date = b.business_date
where
    m.has_missing_price != b.any_missing
    or m.has_stale_price != b.any_stale
    or (b.any_missing and m.market_value_cad is not null)
