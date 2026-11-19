-- An order never fills more than its quantity, and a cancelled order's fills
-- plus its cancelled remainder equal its quantity. Returns the orders that break it.
select
    order_id,
    quantity,
    filled_quantity,
    cancelled_quantity,
    order_status
from {{ ref('fct_orders') }}
where
    filled_quantity + cancelled_quantity > quantity
    or (order_status = 'CANCELLED' and filled_quantity + cancelled_quantity != quantity)
    or (order_status = 'FILLED' and filled_quantity != quantity)
