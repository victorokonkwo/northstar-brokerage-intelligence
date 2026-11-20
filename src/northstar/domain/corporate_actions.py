"""Corporate events: dividends (roadmap 5C-a) and option expiry (5C-b), ADR 013.

An account is entitled to a cash dividend on the shares it holds settled at
the end of the record date. Under T+1 the ex-dividend date is the record date,
so those are exactly the shares it held at the close of the day before the
ex-date. The rate is in whole cents and holdings are whole shares, so every
allocation is exact: nothing is rounded (ADR 004).
"""

from collections.abc import Mapping
from datetime import date, time
from decimal import Decimal
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from northstar.common.exceptions import PolicyNotFoundError
from northstar.domain.events import DividendAllocation, OptionRight

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


def intrinsic_value(right: OptionRight, strike: Decimal, underlying_close: Decimal) -> Decimal:
    """What exercising one share's worth of the option is worth now, never negative.

    A call: close - strike; a put: strike - close.
    """
    if right is OptionRight.CALL:
        return max(underlying_close - strike, Decimal(0))
    return max(strike - underlying_close, Decimal(0))


class OptionExpiryPolicy(BaseModel):
    """What the brokerage does with clients' long options at expiry (roadmap 5C-b, ADR 013).

    ``auto_exercise_threshold`` is the intrinsic value per share at or above
    which an option is exercised at expiry (OCC's exercise by exception: $0.01).
    With ``close_out_uncovered``, an in-the-money option the account can't
    exercise (a put without the shares, a call it can't fund) is sold at that
    day's close instead.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    effective_from: date
    illustrative: Literal[True]
    auto_exercise_threshold: Annotated[Decimal, Field(ge=0)]
    close_out_uncovered: bool
    processing_time_local: time

    def exercises_at_expiry(
        self, right: OptionRight, strike: Decimal, underlying_close: Decimal, on: date
    ) -> bool:
        """Whether an option with these terms is exercised at expiry on ``on``.

        It must be in the money, by at least the threshold.

        Raises:
            PolicyNotFoundError: ``on`` is before this version takes effect.
        """
        if on < self.effective_from:
            msg = f"option expiry policy {self.version} is effective from {self.effective_from}"
            raise PolicyNotFoundError(msg)
        intrinsic = intrinsic_value(right, strike, underlying_close)
        return intrinsic > 0 and intrinsic >= self.auto_exercise_threshold
