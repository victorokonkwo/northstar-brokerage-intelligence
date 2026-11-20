"""Versioned market-risk policies (config/risk/; owner decisions 2026-11-20).

Every value is illustrative, not a real firm's or regulator's. Each policy has
a version (or, for the VaR method, a model version) that every result records
(W2.7, FR-M3-01, FR-M3-08). Instruments are referred to by symbol, because
instrument IDs depend on the dataset seed.
"""

import math
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from northstar.common.exceptions import PolicyNotFoundError
from northstar.common.money import Currency
from northstar.domain.settlement import AssetClass

Rate = Annotated[Decimal, Field(ge=0, le=1)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MarginRates(_Frozen):
    """A position's margin requirement rate and collateral haircut, as fractions of market value."""

    requirement_rate: Rate
    haircut: Rate


class MarginPolicies(_Frozen):
    """Margin requirement and haircut by asset class, with per-symbol overrides (FR-M3-01/02)."""

    version: str
    effective_from: date
    illustrative: Literal[True]
    by_asset_class: dict[AssetClass, MarginRates]
    by_symbol: dict[str, MarginRates] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _every_asset_class_has_rates(self) -> Self:
        if missing := set(AssetClass) - set(self.by_asset_class):
            msg = f"no margin rates for asset classes {sorted(missing)}"
            raise ValueError(msg)
        return self

    def rates_for(self, asset_class: AssetClass, symbol: str) -> MarginRates:
        """The rates that apply to an instrument: its symbol's override, else its asset class's."""
        return self.by_symbol.get(symbol, self.by_asset_class[asset_class])


class StressScenario(_Frozen):
    """One shock: a relative move for every equity, per-symbol moves, and a vol multiplier."""

    name: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$")]
    equity_shock: Annotated[Decimal, Field(gt=-1)]
    symbol_shocks: dict[str, Annotated[Decimal, Field(gt=-1)]] = Field(default_factory=dict)
    vol_multiplier: Annotated[Decimal, Field(gt=0)]

    def shock_for(self, symbol: str) -> Decimal:
        """The relative price move of an equity: its own shock if listed, else the uniform one."""
        return self.symbol_shocks.get(symbol, self.equity_shock)


class StressScenarios(_Frozen):
    """The versioned stress scenarios (FR-M3-04). ZERO is required: it reproduces KPI-07."""

    version: str
    effective_from: date
    illustrative: Literal[True]
    scenarios: tuple[StressScenario, ...]

    @model_validator(mode="after")
    def _names_unique_and_zero_present(self) -> Self:
        names = [s.name for s in self.scenarios]
        if len(names) != len(set(names)):
            msg = f"scenario names repeat: {names}"
            raise ValueError(msg)
        zero = next((s for s in self.scenarios if s.name == "ZERO"), None)
        if zero is None or zero.equity_shock != 0 or zero.symbol_shocks or zero.vol_multiplier != 1:
            msg = "a ZERO scenario with no shock is required (it must reproduce KPI-07)"
            raise ValueError(msg)
        return self


class RiskLimits(_Frozen):
    """Limits whose breach raises a genuine-exposure alert (FR-M3-06)."""

    version: str
    effective_from: date
    illustrative: Literal[True]
    account_issuer_concentration: Rate
    firm_issuer_concentration: Rate
    account_var_to_equity: Rate
    margin_deficit: bool


class RiskFreeRates(_Frozen):
    """Annual, continuously compounded risk-free rates by currency, for option pricing."""

    version: str
    effective_from: date
    illustrative: Literal[True]
    continuous_rates: dict[Currency, Annotated[Decimal, Field(ge=-1, le=1)]]

    @model_validator(mode="after")
    def _every_currency_has_a_rate(self) -> Self:
        if missing := set(Currency) - set(self.continuous_rates):
            msg = f"no risk-free rate for {sorted(missing)}"
            raise ValueError(msg)
        return self

    def rate(self, currency: Currency, on: date) -> Decimal:
        """The annual continuous rate of ``currency`` that applies ``on`` a date.

        Raises:
            PolicyNotFoundError: ``on`` is before this version takes effect.
        """
        if on < self.effective_from:
            msg = (
                f"risk-free rates {self.version} are effective from {self.effective_from}, not {on}"
            )
            raise PolicyNotFoundError(msg)
        return self.continuous_rates[currency]


class VarMethod(_Frozen):
    """Historical-simulation VaR and ES parameters (OQ-08, resolved 2026-11-20).

    VaR is the k-th worst loss and ES the mean of the k worst losses, with
    k = ceil(window x (1 - confidence)): order statistics, no interpolation.
    """

    model_version: str
    effective_from: date
    window_days: Annotated[int, Field(ge=1)]
    horizon_days: Literal[1]
    var_confidence: Annotated[Decimal, Field(gt=0, lt=1)]
    es_confidence: Annotated[Decimal, Field(gt=0, lt=1)]

    @property
    def var_rank(self) -> int:
        """Which worst loss is the VaR (1 = the worst)."""
        return _rank(self.window_days, self.var_confidence)

    @property
    def es_count(self) -> int:
        """How many of the worst losses the ES averages."""
        return _rank(self.window_days, self.es_confidence)


def _rank(window: int, confidence: Decimal) -> int:
    return max(1, math.ceil(Decimal(window) * (1 - confidence)))
