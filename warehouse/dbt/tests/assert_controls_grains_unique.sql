-- Every controls mart's composite grain is unique. Returns each duplicate.
select
    'mart_exception_monitoring' as model_name,
    business_date || ':' || reconciliation || ':' || exception_type || ':' || severity
        as grain_key
from {{ ref('mart_exception_monitoring') }}
group by business_date, reconciliation, exception_type, severity
having count(*) > 1
union all
select
    'mart_match_rates' as model_name,
    business_date || ':' || currency as grain_key
from {{ ref('mart_match_rates') }}
group by business_date, currency
having count(*) > 1
union all
select
    'mart_settlement_fails' as model_name,
    business_date || ':' || currency || ':' || asset_class as grain_key
from {{ ref('mart_settlement_fails') }}
group by business_date, currency, asset_class
having count(*) > 1
union all
select
    'mart_cash_breaks' as model_name,
    business_date || ':' || currency as grain_key
from {{ ref('mart_cash_breaks') }}
group by business_date, currency
having count(*) > 1
