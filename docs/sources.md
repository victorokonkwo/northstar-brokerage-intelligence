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
| OpenLineage defines a standard job/run/dataset model for pipeline lineage, uses configurable event backends, and documents Airflow and dbt integrations. | [OpenLineage documentation](https://openlineage.io/docs/) (2026-11-18) | Verified (project documentation) |
| OpenTelemetry is a vendor-neutral framework for generating, collecting, and exporting traces, metrics, and logs. AWS documents CloudWatch OTLP endpoints for logs, metrics, and traces and recommends the CloudWatch agent or an OpenTelemetry Collector for export. | [OpenTelemetry documentation](https://opentelemetry.io/docs/); [AWS Distro for OpenTelemetry documentation](https://aws-otel.github.io/docs/introduction); [CloudWatch OTLP endpoints](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-OTLPEndpoint.html) (2026-11-18) | Verified (official project and AWS documentation) |
| dbt Core 1.12.7 with dbt-postgres 1.11.0 installs and runs under Python 3.12 and 3.13. `dbt init --skip-profile-setup` scaffolds the project, and `dbt parse` succeeds on it under both versions. | Local runs, 2026-11-18 | Verified |
| These packages install with `uv pip install` and import on CPython 3.12.16 and 3.13.18 (macOS arm64): dbt-core 1.12.7, statsmodels 0.15.0, lightgbm 4.7.0, mlflow 3.18.0, exchange-calendars 4.13.3, numpy 2.5.5, pandas 3.1.1, scipy, fastapi, streamlit, sqlfluff 4.5.0, import-linter 2.17. | Local runs in a scratch venv, 2026-11-18 | Verified |
| On macOS, LightGBM 4.7.0 fails to load without the OpenMP runtime (`Library not loaded: @rpath/libomp.dylib`). This happened on both Python versions. Homebrew's `libomp` is the usual fix. | Local run, 2026-11-18 (error observed; fix not yet tested) | Verified (error); Unverified (fix) |
| CPython end of life: 3.12 on 2028-10-31, 3.13 on 2029-10-31. | [endoflife.date API](https://endoflife.date/api/python.json) | Secondary (2026-11-18). Primary source: the PEP 693 / PEP 719 release schedules. |
| `apache-airflow` 3.4.0 is the latest release. Its PyPI metadata says `requires_python` is `>=3.11, !=3.15`. Docker Hub has `apache/airflow:3.4.0-python3.12` and `3.4.0-python3.13`. The constraint files `constraints-3.4.0/constraints-3.12.txt` and `constraints-3.13.txt` exist, and the 3.13 file pins `apache-airflow-providers-postgres==7.1.1` and `apache-airflow-providers-amazon==9.39.0`. The latest 2.x release is 2.11.2 (uploaded 2026-03-14). | [PyPI](https://pypi.org/project/apache-airflow/); [Docker Hub](https://hub.docker.com/r/apache/airflow); [constraints branch](https://github.com/apache/airflow/tree/constraints-3.4.0) | Verified (2026-11-18) |
| MinIO's GitHub repository (`minio/minio`, AGPL-3.0) is archived. MinIO stopped publishing free Community Edition Docker images in October 2025 and now distributes source code only. | GitHub API (`archived=true`, 2026-11-18); [Gigazine](https://www.gigazine.net/gsc_news/en/20251023-minio-stops-distributing-free-docker-images); [Chainguard](https://www.chainguard.dev/unchained/secure-and-free-minio-chainguard-containers) | Verified (archive status); Secondary (image distribution) |
| Since 2026-03-23, LocalStack ships as a single image that requires an auth token. | [LocalStack blog — The road ahead](https://blog.localstack.cloud/the-road-ahead-for-localstack/); [LocalStack blog — single image next steps](https://blog.localstack.cloud/localstack-single-image-next-steps/) | Secondary (search summary of the vendor blog, 2026-11-18) |
| SeaweedFS (`seaweedfs/seaweedfs`) is Apache-2.0 licensed and not archived. Release 4.54 was published 2026-11-16, and Docker Hub has `chrislusf/seaweedfs:4.54`. | GitHub API; Docker Hub API (2026-11-18) | Verified |
| Airflow 3.4.0 is tested with PostgreSQL 14, 15, 16, 17 and 18 as its metadata database. | [Airflow 3.4.0 prerequisites](https://airflow.apache.org/docs/apache-airflow/3.4.0/installation/prerequisites.html) (2026-11-19) | Verified |
| The official Airflow 3.4.0 `docker-compose.yaml` uses CeleryExecutor with Redis, the FAB auth manager, `api-server`, `scheduler`, `dag-processor` and `triggerer` services, and creates the admin user through `_AIRFLOW_DB_MIGRATE` and `_AIRFLOW_WWW_USER_*` in an init container. The `apache/airflow:3.4.0-python3.13` image includes the FAB 3.10.2, Postgres 7.1.1, Amazon 9.39.0 and Standard 1.21.0 providers. | [Official compose file](https://airflow.apache.org/docs/apache-airflow/3.4.0/docker-compose.yaml); `pip list` in the image (2026-11-19) | Verified |
| The `postgres:18.7` image sets `PGDATA=/var/lib/postgresql/18/docker` and declares the volume `/var/lib/postgresql`. | `docker image inspect` (2026-11-19) | Verified |
| SeaweedFS 4.54 `weed mini` runs master, volume, filer and S3 in one process and pre-creates buckets with `-bucket`. When `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` are set, it adds them as the admin identity: anonymous requests get `AccessDenied`, and unknown keys get `InvalidAccessKeyId`. `GET /healthz` on the S3 port returns 200. Telemetry is turned off with `-master.telemetry=false`; the startup log suggests `-telemetry=false`, but `mini` rejects that flag. | Local runs of `chrislusf/seaweedfs:4.54` (2026-11-19) | Verified |
| uv 0.13.7 ships `uv audit`, an experimental command (it needs `--preview-features audit-command` to run without a warning) that checks the lock file against OSV. It exits 0 on the project's lock file ("no known vulnerabilities … in 86 packages") and exits 1 on a probe script that pins `requests==2.19.0`, where it reported 19 known vulnerabilities. | Local runs, 2026-11-19 | Verified |
| `actions/checkout` v7.0.1 is commit `3d3c42e5aac5ba805825da76410c181273ba90b1`, and `astral-sh/setup-uv` v10.3.3 is commit `68559e99cc807c1eb7e9238fd43a15bba460eec2`. Both run on `node24`. | GitHub API (`git/ref/tags`), 2026-11-19 | Verified |
| `import-linter` 2.17 is BSD-2-Clause licensed and requires Python ≥ 3.11. | [PyPI](https://pypi.org/project/import-linter/) (2026-11-18) | Verified |

### Calendars (OQ-04 research)

| Fact | Source | Status |
|---|---|---|
| In `exchange-calendars` 4.13.3, the `XTSE` and `XNYS` calendars exist, but they model **trading sessions**, not settlement days. Their default range is a rolling window: on 2026-11-18 it ran from 2006-11-20 to 2027-11-18, so it depends on the date it is computed and on the library version. | Local run, 2026-11-18 | Verified |
| For 2026, `exchange-calendars` reports: 2026-10-12 is an XNYS session but not an XTSE session; 2026-11-11 is a session on both; 2026-08-03 and 2026-12-28 are XNYS sessions but not XTSE sessions. | Local run, 2026-11-18 | Verified (library output only; not checked against exchange notices) |
| SIFMA's recommended US bond-market holidays for 2026 include Columbus Day (2026-10-12) and Veterans Day (2026-11-11), days when the NYSE trades. Whether US *equity* settlement (DTC) happens on those days is not established. | [SIFMA holiday schedule](https://www.sifma.org/resources/guides-playbook/holiday-schedule) | Secondary (search summary, 2026-11-18); DTC equity settlement on those days is **Unverified** |
| The dbt CLI reports that v2 is the default for new installations, with v1 still available. Upgrading requires running cleanly on the latest v1.x first. | dbt CLI output (2026-11-18); [dbt — Upgrading to v2](https://docs.getdbt.com/docs/dbt-versions/dbt-upgrade/upgrading-to-v2) | Verified (CLI); docs seen via search |
| dbt v2 support for the Postgres and Redshift adapters | — | Unverified |
| Amazon Redshift `DECIMAL`: maximum precision 38, default 18. Values with ≤ 19 digits of precision are stored as 8-byte integers, and values with 20–38 digits as 16-byte integers. `SUM` of `DEC(p, s)` returns `DEC(38, s)`. `MEDIAN`/`PERCENTILE_CONT` may be inaccurate at maximum precision. | [Redshift numeric types](https://docs.aws.amazon.com/redshift/latest/dg/r_Numeric_types201.html); [Redshift SUM](https://docs.aws.amazon.com/redshift/latest/dg/r_SUM.html); [Redshift MEDIAN](https://docs.aws.amazon.com/redshift/latest/dg/r_WF_MEDIAN.html) | Verified (AWS docs, via search, 2026-11-18) |
| ISO 4217: CAD (numeric 124) and USD (numeric 840) both have 2 minor-unit decimal places. | [SIX Group ISO 4217 list one (XML)](https://www.six-group.com/dam/download/financial-information/data-center/iso-currrency/lists/list-one.xml), published 2026-09-17, fetched 2026-11-18 | Verified |
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
