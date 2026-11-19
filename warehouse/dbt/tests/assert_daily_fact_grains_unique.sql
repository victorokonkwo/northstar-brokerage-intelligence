-- The composite grain of every daily fact and mart is unique.
-- Returns the model and key of each duplicate.
select
    'fct_fx_rates_daily' as model_name,
    base_currency || ':' || quote_currency || ':' || rate_date as grain_key
from {{ ref('fct_fx_rates_daily') }}
group by base_currency, quote_currency, rate_date
having count(*) > 1
union all
select
    'fct_market_prices_daily' as model_name,
    instrument_id || ':' || business_date as grain_key
from {{ ref('fct_market_prices_daily') }}
group by instrument_id, business_date
having count(*) > 1
union all
select
    'fct_positions_daily' as model_name,
    account_id || ':' || instrument_id || ':' || business_date as grain_key
from {{ ref('fct_positions_daily') }}
group by account_id, instrument_id, business_date
having count(*) > 1
union all
select
    'mart_daily_client_cash' as model_name,
    account_id || ':' || currency || ':' || business_date as grain_key
from {{ ref('mart_daily_client_cash') }}
group by account_id, currency, business_date
having count(*) > 1
union all
select
    'mart_daily_finance' as model_name,
    currency || ':' || business_date as grain_key
from {{ ref('mart_daily_finance') }}
group by currency, business_date
having count(*) > 1
union all
select
    'mart_daily_executions' as model_name,
    market || ':' || currency || ':' || business_date as grain_key
from {{ ref('mart_daily_executions') }}
group by market, currency, business_date
having count(*) > 1
union all
select
    'mart_daily_positions' as model_name,
    account_id || ':' || business_date as grain_key
from {{ ref('mart_daily_positions') }}
group by account_id, business_date
having count(*) > 1
