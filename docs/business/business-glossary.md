# Business glossary

These are the definitions used across code, SQL, and docs. When a term is used in a column or function name, it means exactly what is written here. If a definition changes, update this file in the same change.

Terms marked † simplify real-world practice for the simulation. The simplification is described in the [domain model](../architecture/domain-model.md).

## Brokerage lifecycle

| Term | Definition |
|---|---|
| **Account** | A client's brokerage account, with a type (e.g., cash or margin), a base currency, and margin eligibility. Versioned over time (SCD Type 2). |
| **Instrument** | A tradable security or contract: an equity, ETF, or listed option. Identified by `instrument_id` and described by listing exchange, currency, and asset class. |
| **Order** | A client instruction to buy or sell a quantity of an instrument. It has a lifecycle status (e.g., placed, partially filled, filled, cancelled). An order is not a trade. |
| **Execution (fill)** | A single completed trade of some or all of an order's quantity at a price. An order may have many executions. |
| **Partial fill** | An execution for less than the order's remaining quantity. |
| **Trade date (T)** | The business date on which an execution occurs. |
| **Settlement date** | The business date on which cash and securities are contractually exchanged. It is computed from the trade date and the instrument's settlement cycle on the settlement calendar. |
| **T+1** | Settlement one business day after trade date. Canada (May 27, 2024) and the US (May 28, 2024) moved standard equity settlement from T+2 to T+1; see [sources](../sources.md). |
| **Settlement obligation** | The amount of cash and securities owed on the settlement date as a result of executions. Created on trade date, then closed by settlement. |
| **Failed / late settlement** | An obligation not settled on its contractual settlement date. |
| **Position** | The quantity of an instrument an account holds as of a date. Kept separately as trade-date and settlement-date positions. |
| **Cost basis** | The total acquisition cost of a position, including fees where the policy says so. The method (e.g., average cost) is defined in the domain model. |
| **Market value** | Position quantity × price as of a valuation timestamp, in the instrument's currency. |
| **Commission / fee** | A charge to the client for a service (e.g., a trade commission), or a cost charged to the broker by a third party (e.g., a clearing fee). The two are kept distinct. |

## Accounting

| Term | Definition |
|---|---|
| **General ledger (GL)** | The set of all accounts in the chart of accounts and their journal lines. It is the financial system of record ([ADR 002](../adr/002-ledger-system-of-record.md)). |
| **Chart of accounts** | The versioned list of GL accounts and their types (asset, liability, equity, revenue, expense). |
| **Journal batch** | A group of journal lines posted together for one business event. It must balance within each currency. |
| **Journal line** | A single debit or credit to one GL account, in one currency, for one amount. |
| **Double entry** | Every business event is recorded as equal debits and credits. |
| **Client cash payable** † | The broker's liability for cash it holds on behalf of clients (a client "free credit balance"). |
| **Client debit balance / margin loan** † | A negative client cash balance. Economically it is a loan from the broker to the client, secured by the client's securities, and is reported as a receivable. |
| **Stock record** † | The securities-quantity ledger. For each instrument, the quantities held by clients equal the quantities at locations such as the depository, custodian, or pending settlement. |
| **Reversal** | A journal batch that exactly offsets a previously posted batch. It is the only way to correct a posted entry. |
| **Reporting currency** | The currency in which consolidated results are presented: CAD. |
| **Translation** | Converting amounts in an original currency to the reporting currency at a dated FX rate. The original amount is always kept as well. |

## Reconciliation and controls

| Term | Definition |
|---|---|
| **Reconciliation** | Comparing two independently produced records of the same economic facts, e.g., internal executions vs. clearing confirmations. |
| **Match** | Records that refer to the same fact and agree within tolerance. Matching is exact on stable IDs, composite on deterministic keys, or tolerance-based within a configured threshold. |
| **Break / exception** | A discrepancy found by reconciliation: a missing record, a duplicate, or a value mismatch. |
| **Severity** | The impact classification of an exception (see domain model §7). It determines whether the exception blocks publication. |
| **Exception aging** | Business days since an exception was first detected. |
| **Auto-resolution** | Closing an exception automatically under a named, versioned rule. It is always recorded in the audit trail. |
| **Override** | A manual resolution. It requires a reason, an actor, and a timestamp. |
| **Publication gate** | The control that stops a reporting snapshot from being marked published while blocking exceptions or failed controls are open. |

## Market risk

| Term | Definition |
|---|---|
| **Account equity** | Market value of long positions + cash balance (negative if there is a debit balance) − market value of short positions. In base currency. |
| **Margin requirement** | The minimum equity an account must hold against its positions, computed from versioned margin-rate policy. |
| **Margin excess / deficit** | Account equity − margin requirement. A negative value is a deficit and triggers a margin alert. |
| **Equity ratio** | Account equity ÷ long market value. Used as a simple illustrative monitoring metric. |
| **Haircut** | The percentage reduction applied to a security's market value when valuing it as collateral. |
| **Loan value** | Market value × (1 − haircut). The maximum that can be borrowed against a position. |
| **Concentration** | The share of an account's (or the firm's) collateral market value in a single issuer. |
| **Value at Risk (VaR)** | For a confidence level α and horizon h, the loss that is not exceeded with probability α. Reported as a positive number. |
| **Historical simulation** | A VaR method that revalues the current portfolio under historical market returns from a lookback window. |
| **Expected Shortfall (ES)** | The average loss in the tail beyond the VaR quantile at the same confidence level. |
| **Stress test** | Revaluing portfolios under a defined, versioned market scenario, e.g., equities −20%. |
| **VaR backtest** | Comparing daily VaR forecasts with realized P&L and counting exceptions. Tested with Kupiec's proportion-of-failures and Christoffersen's independence tests. |
| **Greeks** | Sensitivities of an option's value: Delta (to the underlying price), Gamma (Delta's sensitivity to the underlying price), Vega (to volatility), and Theta (to the passage of time). |
| **European / American option** | A European option can be exercised only at expiry. An American option can be exercised at any time up to expiry. Black–Scholes is valid only for European exercise without dividends, unless it is adjusted. |

## Treasury and forecasting

| Term | Definition |
|---|---|
| **Known obligation** | A future cash flow already contractually fixed, e.g., unsettled trades due on a known settlement date. |
| **Uncertain flow** | A future cash flow that must be forecast, e.g., client deposits and withdrawals. |
| **Net cash flow** | Inflows − outflows for a currency over a period. |
| **Liquidity headroom** | Available liquid resources − projected net outflows under a given scenario and horizon. |
| **Horizon** | How far ahead a forecast predicts: 1, 7, or 30 business days. |
| **Prediction interval** | A range expected to contain the actual value with a stated probability (e.g., an 80% interval from P10 to P90). |
| **Interval coverage** | The share of actual values that fell inside their prediction intervals. Compared with the nominal level. |
| **WAPE** | Weighted absolute percentage error: Σ\|actual − forecast\| ÷ Σ\|actual\|. |
| **Pinball (quantile) loss** | The loss function used to evaluate quantile forecasts. |
| **Walk-forward (rolling-origin) validation** | Evaluation in which each forecast is trained only on data available before its origin date. |
| **Seasonal naive** | A baseline forecast equal to the value one season ago, e.g., the same weekday last week. |

## Finance

| Term | Definition |
|---|---|
| **Revenue** | Income recognized in the ledger: commissions, FX spread, margin interest, and other fees. |
| **Direct cost** | A cost directly attributable to a product: clearing, execution, and funding costs. |
| **Contribution margin** | Product revenue − product direct costs. |
| **Allocated cost** | An indirect cost assigned to products by a documented allocation method. Reported separately from contribution margin. |
| **Active account** | An account with at least one client-initiated execution or cash movement in the trailing 90 calendar days. (Project definition; see KPI dictionary.) |
| **Restatement** | Publishing a new version of a previously published figure because of a correction, with the reason recorded. Earlier versions stay readable. |

## Data and platform

| Term | Definition |
|---|---|
| **Bronze / Silver / Gold** | Raw immutable source data (S3) / validated, conformed core models (dbt staging, intermediate, core) / curated, consumer-facing marts. |
| **Grain** | What one row of a table represents. Every model documents its grain. |
| **SCD Type 2** | A slowly changing dimension that keeps full history using `valid_from` / `valid_to` rows. |
| **Bitemporal** | Tracking both when a fact was true (event or effective time) and when the platform knew it (recorded or ingested time). |
| **Late-arriving record** | A record ingested after the snapshot for its business date was published. |
| **Quarantine** | A holding area for records that failed schema or business-rule validation. They are kept with the failure reason, never dropped. |
| **Idempotent** | Running an operation more than once has the same effect as running it once. |
| **Lineage** | The traceable path from a reported figure back to the source records and transformation versions that produced it. |
| **CIRO** | Canadian Investment Regulatory Organization, the self-regulatory organization for Canadian investment dealers. Its rules are used here as domain reference only. |
