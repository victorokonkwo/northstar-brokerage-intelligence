# External sources and verification status

The design relies on the external facts listed here. **Verification status:**

- **Verified:** checked against a primary source or the installed tool. The date is recorded.
- **Secondary:** seen in reputable secondary sources only. Confirm against the primary source before relying on it in code.
- **Unverified:** an assumption. It must not be built on until it is verified.

When code depends on one of these facts, it references this file, and the fact must be `Verified` first.

## Market structure and regulation

| Fact | Source | Status |
|---|---|---|
| Canada moved standard equity settlement from T+2 to T+1. Last T+2 trade date: Fri 2024-05-24. First T+1 trade date: Mon 2024-05-27. The transition was coordinated by the Canadian Capital Markets Association (CCMA). | [CDS — T+1](https://cds.ca/solutions/cds-key-initiatives/t-1); [CCMA release via Newswire](https://newswire.ca/news-releases/ccma-says-capital-markets-transition-to-shorter-securities-settlement-a-success-839210084.html) | Secondary (search results, 2026-11-18). Confirm on the CCMA/CDS pages. |
| The US moved to T+1 on 2024-05-28 (after the Memorial Day holiday on 2024-05-27). | Reported in the same search results; the primary source is the SEC's amendment to Rule 15c6-1 | Secondary. Confirm on sec.gov. |
| Options settlement cycle (CA: CDCC; US: OCC) | — | Unverified (OQ-03) |
| CIRO's Investment Dealer and Partially Consolidated (IDPC) Rules contain margin requirements in **Series 5000**: 5100 application and definitions, 5200 debt, 5300 equity securities and index products, 5400 other products, 5500 underwriting, 5600–5700 offsets, 5800–5900 agreement-related. Minimum rates for securities on the reduced-margin list are in subsection 5310(1). | [CIRO — Rule Consolidation Project Phase 2](https://www.ciro.ca/newsroom/publications/rule-consolidation-project-phase-2); [CIRO — List of securities eligible for reduced margin](https://www.ciro.ca/node/27256) | Secondary (search summary, 2026-11-18). The rule text was not read. |
| CIRO is consolidating its rulebooks into a single set of "CIRO Rules". A republished proposal had comments due 2026-06-12. Whether it has taken effect is unknown. | [CIRO — Proposed CIRO Rules](https://www.ciro.ca/node/32241) | Secondary. Check the current status before citing rule numbers. |

**Use in this project.** CIRO rules are **domain reference only**. The margin engine is an illustrative simulation with its own configurable rates. It does not implement CIRO margin requirements and must not be described as doing so.

## Data sources

| Fact | Source | Status |
|---|---|---|
| The Bank of Canada Valet API provides FX and other series in JSON/CSV/XML with no API key. The USD/CAD daily rate is series `FXUSDCAD`. Observations endpoint pattern: `/valet/observations/FXUSDCAD/json`, with optional `start_date` and `end_date`. | [Valet API docs](https://www.bankofcanada.ca/valet/docs) | Secondary (third-party descriptions, 2026-11-18). Confirm against the official docs and a live call in Phase 2. |
| Bank of Canada exchange rates are indicative (not transactional) and published for Bank of Canada business days only. | Bank of Canada exchange rates page | Unverified. Confirm in Phase 2. |
| Bank of Canada series IDs for interest rates (e.g., policy rate) | Valet `/valet/lists/series/json` | Unverified. Look up when needed. |

## Tooling

| Fact | Source | Status |
|---|---|---|
| dbt Core 1.12.7 with dbt-postgres 1.11.0 installs and runs under Python 3.12. `dbt init --skip-profile-setup` scaffolds the project, and `dbt parse` succeeds on it. | Local run, 2026-11-18 | Verified |
| The dbt CLI reports that v2 is the default for new installations, with v1 still available. Upgrading requires running cleanly on the latest v1.x first. | dbt CLI output (2026-11-18); [dbt — Upgrading to v2](https://docs.getdbt.com/docs/dbt-versions/dbt-upgrade/upgrading-to-v2) | Verified (CLI); docs seen via search |
| dbt v2 support for the Postgres and Redshift adapters | — | Unverified |
| Amazon Redshift `DECIMAL`: maximum precision 38, default 18. Values with ≤ 19 digits of precision are stored as 8-byte integers, and values with 20–38 digits as 16-byte integers. `SUM` of `DEC(p, s)` returns `DEC(38, s)`. `MEDIAN`/`PERCENTILE_CONT` may be inaccurate at maximum precision. | [Redshift numeric types](https://docs.aws.amazon.com/redshift/latest/dg/r_Numeric_types201.html); [Redshift SUM](https://docs.aws.amazon.com/redshift/latest/dg/r_SUM.html); [Redshift MEDIAN](https://docs.aws.amazon.com/redshift/latest/dg/r_WF_MEDIAN.html) | Verified (AWS docs, via search, 2026-11-18) |
| Python `decimal` default context: precision 28, rounding `ROUND_HALF_EVEN`. `0.1 + 0.2 == 0.3` is `False` for floats. | Local interpreter check, 2026-11-18 | Verified |

## Quantitative methods (literature)

| Method | Reference | Status |
|---|---|---|
| Black–Scholes option pricing | Black, F. & Scholes, M. (1973). "The Pricing of Options and Corporate Liabilities." *Journal of Political Economy*, 81(3). | Standard reference |
| VaR backtest: proportion of failures | Kupiec, P. (1995). "Techniques for Verifying the Accuracy of Risk Measurement Models." *Journal of Derivatives*, 3(2). | Standard reference |
| VaR backtest: independence / conditional coverage | Christoffersen, P. (1998). "Evaluating Interval Forecasts." *International Economic Review*, 39(4). | Standard reference |
| Forecast evaluation and time-series cross-validation | Hyndman, R. J. & Athanasopoulos, G. *Forecasting: Principles and Practice* (3rd ed.), OTexts. | Standard reference |

## Role alignment (context only)

The project's scope was shaped by a public job posting for a Senior Data Scientist, Finance, Brokerage & Market Risk role (Remote, Canada) on Ashby. It is used only to prioritize which capabilities to build. The project is not affiliated with the employer.
