-- The gross amount read from each execution's journal lines equals
-- quantity x price x multiplier rounded half-even (ADR 004), and its commission
-- and clearing fee equal the execution's own. Every execution is covered.
-- Returns the executions that break it.
select
    e.execution_id,
    a.gross_amount,
    {{ round_half_even('e.quantity * e.price * e.multiplier', 2) }} as expected_gross
from {{ ref('fct_executions') }} as e
left join {{ ref('int_execution_amounts') }} as a on e.execution_id = a.execution_id
where
    a.execution_id is null
    or a.gross_amount != {{ round_half_even('e.quantity * e.price * e.multiplier', 2) }}
    or a.commission != e.commission
    or a.clearing_fee != e.clearing_fee
