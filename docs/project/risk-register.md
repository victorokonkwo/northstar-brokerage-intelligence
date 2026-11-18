# Risk register

| | |
|---|---|
| Last updated | 2026-11-18 |

Likelihood (L) and impact (I) are rated 1 (low) to 3 (high). Review the register at every phase gate.

| ID | Risk | L | I | Mitigation | Status |
|---|---|---|---|---|---|
| R-01 | **Scope overrun.** Five modules in 18 weeks is aggressive for one developer. | 3 | 3 | Vertical slice first. Phase gates are strict. Cut breadth (fewer instruments or scenarios) before depth (correctness and tests). Event-driven risk and Power BI are optional extras. | Open |
| R-02 | **Accounting model errors** produce plausible but wrong figures downstream. | 2 | 3 | Ledger-first design (ADR 002). Property tests on every posting rule. Hand-worked examples in the domain model. The financial correctness gate blocks later phases. | Open |
| R-03 | **Synthetic data is unrealistic**, so the models and dashboards prove little. | 2 | 2 | Parameterize behavior (seasonality, volatility-driven activity). Calibrate market prices against public data where licensing allows. Document the assumptions. | Open |
| R-04 | **Reconciliation tests itself** (compares a dataset with a copy of itself). | 2 | 3 | External records are produced by a separate simulator from canonical events (FR-SIM-02). The defect manifest is the ground truth. | Open |
| R-05 | **Misrepresentation.** Illustrative targets or synthetic results are presented as achieved, or the work implies an affiliation with a real firm. | 2 | 3 | The honesty rules (CONTRIBUTING.md, W1). Numbers need a reproducible command. Disclaimers in README and on reports. No real-firm branding. | Open |
| R-06 | **Regulatory overclaiming.** Margin or reports described as compliant. | 2 | 3 | Every regulatory artifact is labeled a simulation. CIRO rules are cited as reference only (sources.md). | Open |
| R-07 | **Forecast leakage** inflates accuracy. | 2 | 2 | Walk-forward splits with a leakage test (FR-M4-04). Always compare against the baseline. | Open |
| R-08 | **Model risk.** Black–Scholes misapplied to American options; VaR assumptions undocumented. | 2 | 2 | FR-M3-05 restricts Black–Scholes to European exercise. Methodology docs and model cards list the limitations. Backtests. | Open |
| R-09 | **AWS cost overrun.** | 2 | 2 | Develop locally first. Budget alerts before resources. Redshift Serverless sized small. Tear down dev environments when idle. | Open |
| R-10 | **Postgres/Redshift dialect drift** breaks cloud builds. | 2 | 2 | Cross-database macros. A Redshift test pass before release (ADR 005). | Open |
| R-11 | **dbt v1 → v2 migration.** dbt now defaults to v2, and our adapter support is unverified. | 2 | 1 | Pin dbt Core 1.12.x. Keep the project free of deprecation warnings. Re-evaluate when adapter support is confirmed (ADR 005). | Open |
| R-12 | **Python version compatibility.** The newest CPython on the dev machine is ahead of data-stack support. | 2 | 1 | Pin the interpreter through uv (Sprint 1 decision). | Open |
| R-13 | **External data availability.** The Bank of Canada Valet API changes or is unavailable. | 1 | 2 | Cache raw responses in the raw layer. Ingestion is idempotent and retries. Fixtures for tests (no network in unit tests). | Open |
| R-14 | **Decimal performance** in bulk Python processing. | 2 | 1 | Aggregate in SQL. Benchmark in Phase 3. ADR 004 allows float for analytics with an explicit boundary. | Open |
| R-15 | **Settlement and calendar facts wrong**, e.g., T+1 effective dates or holidays. | 1 | 2 | Effective-dated settlement config. Facts recorded and verified in sources.md. | Open |
| R-16 | **Secrets leak** into the repository. | 1 | 3 | `.gitignore`, a pre-commit secret scan, and a CI scan (Phase 1). SECURITY.md procedure. | Open |
