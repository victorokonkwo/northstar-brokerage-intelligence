# Project charter — Northstar Brokerage Intelligence

| | |
|---|---|
| Status | Approved by owner 2026-11-18 |
| Phase | 0 — Business architecture (done) |
| Last updated | 2026-11-18 |

## 1. Purpose

Build one independently deployable, testable, and auditable platform that shows end-to-end ownership of a brokerage's finance and market-risk analytics: trusted data marts, financial controls, quantitative risk, treasury forecasting, and executive reporting.

The project is a portfolio piece aimed at senior data science roles in brokerage finance and market risk. One example is the Senior Data Scientist, Finance, Brokerage & Market Risk role at a Canadian online brokerage. That role emphasizes financial and risk data marts, automated brokerage reporting, forecasting, collateral monitoring, and support for regulatory reporting. **The project is independent and not affiliated with any firm.**

## 2. Objectives

1. **Trusted financial data.** Ledger-derived balances, positions, and cash, with executable integrity tests and lineage to source events.
2. **Financial controls.** Automated reconciliation against independently generated external records, with an auditable exception lifecycle.
3. **Defensible risk models.** Margin, VaR, Expected Shortfall, stress testing, and options analytics, each validated against reference cases and backtests, with limitations documented.
4. **Honest forecasting.** Liquidity forecasts evaluated against transparent baselines using walk-forward validation, with uncertainty reported.
5. **Decision-ready reporting.** KPI definitions that reconcile to the ledger and support drilldown to individual transactions.
6. **Senior engineering practice.** ADRs, CI, infrastructure as code, runbooks, model cards, and reproducible results.

## 3. Modules and stakeholders

| Module | Business question it answers | Persona |
|---|---|---|
| M1 Data Foundation | "Can I trust this number, and where did it come from?" | **Analytics engineer.** Owns marts, freshness, and data quality. |
| M2 Financial Controls | "Do our records agree with the clearing firm, custodian, and bank? What is unresolved, and how much is at stake?" | **Brokerage operations analyst.** Works exceptions daily. Needs severity, age, and monetary impact. |
| M3 Market Risk | "Which accounts are under-margined? What happens if equities fall 20%? Where is concentration building?" | **Market risk manager.** Needs explainable alerts, policy versions, and model assumptions. |
| M4 Treasury Intelligence | "How much liquidity do we need over the next 1/7/30 days, and how bad could it get?" | **Treasury analyst.** Needs known obligations separated from uncertain flows, plus intervals and scenarios. |
| M5 Finance Intelligence | "Which products make money after direct costs, and is the reported figure final?" | **Finance lead / CFO office.** Needs reconciled KPIs, restatement history, and reporting status. |

## 4. Flagship end-to-end scenario

This scenario is the main integration test (`tests/end_to_end/`, to be built) and the demo script.

1. A client opens a margin-eligible account and deposits **CAD 25,000**.
2. The client converts CAD to USD and buys a US-listed equity. The order fills in two partial executions.
3. The platform posts balanced journal entries on trade date, creates a settlement obligation (T+1), and settles it.
4. The client buys more on margin, creating a debit balance (a margin loan).
5. The client buys a listed option.
6. The external clearing record for one execution carries an **incorrect fee**. Reconciliation detects it, classifies it, and blocks publication of the day's financial snapshot.
7. A **−20% equity shock** is applied. Margin excess or deficit, stress loss, and option exposure are recalculated and alerts are raised.
8. The treasury forecast is refreshed. Known settlement obligations are separated from forecast client flows.
9. The fee exception is resolved with an audited reason. The snapshot is published as a versioned record. Reported revenue traces back to the individual journal lines.

Every number shown in the demo must come from running the platform on the seeded dataset.

## 5. Scope

### In scope

- A deterministic synthetic brokerage covering accounts, instruments (equities, ETFs, listed options), CAD and USD, deposits and withdrawals, orders, partial fills, cancellations, fees, FX conversion, settlement, margin lending, and interest.
- Independently simulated external records (clearing confirmations, custody snapshots, bank statements, fee records), with labeled injected defects.
- Public FX data from the Bank of Canada Valet API.
- The five modules M1–M5, four Streamlit dashboards, a FastAPI read API, Airflow orchestration, and AWS deployment through Terraform.

### Out of scope

- Real customer data, real broker or clearing connectivity, and order routing or execution venues.
- Intraday streaming risk in the first release. Batch comes first ([ADR 003](../adr/003-batch-first-processing.md)), and event-driven updates are an optional later extension.
- Certified regulatory calculations or submission-ready regulatory reports. All regulatory content is illustrative.
- Tax reporting, corporate actions beyond what is needed for the scenario (to be decided in Phase 2), short selling, crypto, and mutual funds.
- Full IFRS financial statements. The accounting model is a simplified management ledger (see the domain model).

## 6. Assumptions and constraints

- One developer. The roadmap is ambitious: 18 weeks, assuming prior experience with the stack.
- Cloud spend must be minimal. Develop locally first, keep cloud resources small, and set budget alerts before provisioning anything.
- Effort allocation, following the role's emphasis:

  | Area | Share |
  |---|---|
  | Data architecture, SQL, dbt, reliability | 45% |
  | Financial accounting and reconciliation | 20% |
  | Market risk and collateral analytics | 20% |
  | Treasury modeling and forecasting | 10% |
  | Reporting and executive communication | 5% |

## 7. Success criteria

The release gate (Phase 8) passes when a reviewer can, without help from the author:

1. clone the repository and start the local stack with one command,
2. run every test suite and see it pass,
3. run the flagship scenario and inspect each module's outputs,
4. trace a published KPI back to source journal lines and events.

The measurable targets are in [non-functional-requirements.md](../architecture/non-functional-requirements.md).

## 8. Governance

- Decisions are recorded as ADRs in [`docs/adr/`](../adr/).
- Each phase ends with an exit gate ([roadmap](../project/roadmap.md)). **If the accounting or data-quality gates fail, no work proceeds on forecasts or dashboards built on that data.**
- Risks are tracked in the [risk register](../project/risk-register.md).
