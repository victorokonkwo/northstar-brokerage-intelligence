-- INV-07: every amount translated from USD to CAD names a published rate,
-- dated on or before its business date, with that rate's value and policy
-- version. Returns the translated rows that break it.
with translated as (
    select
        'fct_positions_daily' as model_name,
        business_date,
        fx_rate,
        fx_rate_date,
        fx_policy_version
    from {{ ref('fct_positions_daily') }}
    where currency = 'USD'
    union all
    select
        'mart_daily_client_cash' as model_name,
        business_date,
        fx_rate,
        fx_rate_date,
        fx_policy_version
    from {{ ref('mart_daily_client_cash') }}
    where currency = 'USD'
    union all
    select
        'mart_daily_finance' as model_name,
        business_date,
        fx_rate,
        fx_rate_date,
        fx_policy_version
    from {{ ref('mart_daily_finance') }}
    where currency = 'USD'
)

select t.*
from translated as t
left join {{ ref('fct_fx_rates_daily') }} as r
    on
        t.fx_rate_date = r.rate_date
        and r.base_currency = 'USD'
        and r.quote_currency = 'CAD'
where
    r.rate_date is null
    or t.fx_rate_date > t.business_date
    or t.fx_rate != r.rate
    or t.fx_policy_version is distinct from r.fx_policy_version
