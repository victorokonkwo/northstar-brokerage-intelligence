-- The warehouse's FX fallback (int_fx_rates_applicable) and the brokerage's
-- conversion pricing apply the same policy v1 independently. For every client
-- conversion, the rate the warehouse would apply on that business date must be
-- the reference rate, and the rate date, the conversion was priced at.
-- Returns the conversions where they disagree.
select
    m.cash_movement_id,
    m.business_date,
    m.reference_rate,
    m.rate_date,
    a.rate as applicable_rate,
    a.fx_rate_date as applicable_rate_date
from {{ ref('fct_cash_movements') }} as m
left join {{ ref('int_fx_rates_applicable') }} as a on m.business_date = a.calendar_date
where
    m.movement_type = 'FX_CONVERSION'
    and (
        a.calendar_date is null
        or a.rate != m.reference_rate
        or a.fx_rate_date != m.rate_date
    )
