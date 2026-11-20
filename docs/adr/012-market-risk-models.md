# 012. Market risk models: historical simulation on a synthetic history, and model-consistent options

| | |
|---|---|
| Status | Accepted |
| Date | 2026-11-20 |
| Deciders | Project owner |

## Context

Phase 5 builds module M3 (FR-M3-01 to 08; KPI-07 to 14). Facts about the data, from exploring it on 2026-11-20:

- **History.** The dev dataset has about 60 business days of prices (Q1 2026) and none before. KPI-09/10 need a lookback window (OQ-08, open since Phase 0), and KPI-14 backtests over 250 business days.
- **Calendars.** The calendars start 2026-01-01. Verified 2025 TSX and NYSE calendars weren't available from a primary source: both exchanges' pages now list 2026 onward, and the archive was unreachable ([sources.md](../sources.md)).
- **Option prices.** The simulator gives each option its own random walk, unrelated to its underlying. Its prices can't support implied volatility or Greeks, and can break no-arbitrage bounds.
- **Positions.** Positions are long only: the brokerage refuses short sales.
- **Numerics.** [ADR 004](004-fixed-precision-money.md) allows NumPy float64 for risk analytics, with Decimal at the boundary and outputs labelled as model outputs.

## Decision

Owner decisions, 2026-11-20:

1. **VaR and ES: historical simulation with full revaluation, resolving OQ-08.**
   - **Scenarios:** 250 one-day historical scenarios, the most recent before the as-of date. Each applies one day's returns of every equity and of USD/CAD to today's portfolio, and options are repriced.
   - **VaR at 99%:** the 3rd worst loss, k = ceil(250 × 1%).
   - **ES at 97.5%:** the mean of the 7 worst losses, k = ceil(250 × 2.5%).
   - **No interpolation,** for a conservative, reproducible result. Every result records `model_version` (`config/risk/var_method.yaml`).
2. **A synthetic market history before the window** (`simulation.history`):
   - **What:** a year of equity closes and USD/CAD rates (2025).
   - **Calendar:** a synthetic weekday calendar that makes no claim about real exchange holidays.
   - **Continuity:** each path is walked backward from its anchor, so the window's own prices are unchanged.
   - **Determinism:** the draws come from their own seed stream.
   - **Storage:** external record sets `price_history` and `fx_rate_history`, which nothing reconciles.
   - **Backtest:** KPI-14 runs on a fixed reference portfolio over the history plus the window (300+ days). Account-level backtests over the window alone are flagged as an insufficient sample.
3. **Model-consistent option prices** (5B):
   - **How:** the simulator prices each option with a CRR binomial tree on its underlying's close, with a configured implied volatility and the illustrative risk-free rate of its currency (`config/risk/rates.yaml`).
   - **Conventions:** time to expiry is calendar days / 365, and the simulator's tree has 200 steps (`OPTION_TREE_STEPS`). The risk model reprices with the same steps, so it recovers the configured implied vol up to the rounding of the close to a cent.
   - **Determinism:** the tree uses only operations IEEE 754 rounds correctly, with `exp` from `Decimal` and powers by multiplication, so its prices, and the dataset, stay byte-identical on every machine (NFR-06).
   - **Exercise:** American exercise is priced by the tree. European exercise is priced by Black–Scholes, and the tree must converge to it.
   - **Dividends:** known cash dividends are escrowed: the underlying is reduced by the present value of dividends before expiry.
4. **Issuers** (KPI-13): an equity is its own issuer, and an option's issuer is its underlying's. No issuer column is added.
5. **Policies** (`config/risk/`, illustrative): margin requirement and haircut by asset class with a SYNH override, seven stress scenarios including ZERO, four limits, and risk-free rates. The owner approved the values.

## Consequences

- **VaR rests on synthetic history.** It's only as realistic as that history. The history's volatilities are the instruments' configured daily volatilities, so VaR mostly reflects them.
- **Synthetic dates.** The history has prices on real 2025 holidays, labelled synthetic. If verified 2025 calendars are added later, the history can be regenerated on them.
- **The dataset changes in 5B.** Model-consistent option prices change option closes, premiums, the ledger, and the marts. Locally published snapshots restate; CI starts from scratch.
- **Implied vol isn't always identified.** An American option worth exercising now is priced at its intrinsic value for every low enough vol, so `implied_vol` refuses that price rather than return an arbitrary vol.
- **The order statistics are coarse.** With 250 scenarios, VaR moves in steps of one scenario, an accepted cost of reproducibility.

## Revisit when

- **Verified 2025 calendars** become available, which would let the history use real trading days.
- **A real market-data history** is approved instead of a synthetic one.
- **The window grows** beyond what one-day scenarios cover well, which could call for a filtered or weighted historical simulation.
