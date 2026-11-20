"""Corporate events: dividend entitlement and allocation (roadmap 5C-a, ADR 013).

An account is entitled to a cash dividend on the shares it holds settled at
the end of the record date. Under T+1 the ex-dividend date is the record date,
so those are exactly the shares it held at the close of the day before the
ex-date. The rate is in whole cents and holdings are whole shares, so every
allocation is exact: nothing is rounded (ADR 004).
"""

from collections.abc import Mapping
from decimal import Decimal
from typing import Final

from northstar.domain.events import DividendAllocation

_CENT: Final = Decimal("0.01")


def dividend_allocations(
    amount_per_share: Decimal, settled_at_record: Mapping[str, Decimal]
) -> tuple[DividendAllocation, ...]:
    """Each account's dividend: settled shares at the record date x the rate per share.

    ``settled_at_record`` maps account IDs to their settled quantity at the end
    of the record date. Accounts holding none get no allocation. The result is
    in account order.

    Raises:
        ValueError: The rate isn't whole cents, or a holding isn't whole shares.
    """
    if amount_per_share <= 0 or amount_per_share != amount_per_share.quantize(_CENT):
        msg = f"the rate {amount_per_share} is not a positive amount in whole cents"
        raise ValueError(msg)
    allocations = []
    for account_id in sorted(settled_at_record):
        quantity = settled_at_record[account_id]
        if quantity != quantity.to_integral_value():
            msg = f"{account_id} holds {quantity}: dividends are allocated on whole shares"
            raise ValueError(msg)
        if quantity > 0:
            allocations.append(
                DividendAllocation(
                    account_id=account_id, quantity=quantity, amount=quantity * amount_per_share
                )
            )
    return tuple(allocations)
