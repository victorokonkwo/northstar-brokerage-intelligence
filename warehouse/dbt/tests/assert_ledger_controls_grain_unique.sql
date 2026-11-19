-- Grain of mart_ledger_balance_controls: one row per business_date x currency.
select
    business_date,
    currency,
    count(*) as row_count
from {{ ref('mart_ledger_balance_controls') }}
group by business_date, currency
having count(*) > 1
