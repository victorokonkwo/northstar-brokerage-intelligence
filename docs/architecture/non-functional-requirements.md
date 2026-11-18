# Non-functional requirements

| | |
|---|---|
| Status | Approved targets (2026-11-18). **None measured yet.** |
| Last updated | 2026-11-18 |

These are engineering targets to validate during implementation. They are not claims about current performance. When a target is measured, record the result in `docs/portfolio/performance-results.md` (created when the first measurement exists), along with the command, dataset seed and size, hardware or environment, commit SHA, and date.

Where no number can be set responsibly before a baseline exists, the target says **"set after baseline"**. That is deliberate.

| ID | Dimension | Target | How verified |
|---|---|---|---|
| NFR-01 | Financial accuracy | Zero unexplained differences for accepted journal batches (KPI-01 = 0) | INV-01 tests on every build |
| NFR-02 | Data integrity | No duplicate executions after repeat ingestion of the same inputs | INV-02; replay integration test |
| NFR-03 | Reconciliation | Detect and correctly classify 100% of injected, predefined defect scenarios | test against the defect manifest (FR-SIM-03) |
| NFR-04 | Lineage | 100% of published financial KPIs trace to source records | INV-10 tie-out tests |
| NFR-05 | Reproducibility | Replay gives identical outputs (hash-equal) for identical inputs and code version | INV-09 |
| NFR-06 | Determinism | Same seed → byte-identical synthetic dataset | FR-SIM-01 test |
| NFR-07 | Freshness | Daily marts published by a morning cutoff. Proposed default: 07:00 `America/Toronto`, to be confirmed | freshness checks and pipeline timing logs |
| NFR-08 | Scale | Generate, ingest, transform, and query a dataset of ≥ 1,000,000 executions | benchmark script; results recorded |
| NFR-09 | Query performance | p95 latency for dashboard mart queries: **set after baseline** at load-test size | benchmark script |
| NFR-10 | Pipeline runtime | End-to-end daily run at dev size: **set after baseline** | Airflow task durations |
| NFR-11 | Risk model validity | Reference cases, numerical checks (e.g., put–call parity), and backtests pass | `tests/quantitative_validation/` |
| NFR-12 | Forecast evaluation | Walk-forward results reported against the seasonal-naive baseline, with WAPE and interval coverage. **No accuracy target is fixed in advance.** | evaluation report |
| NFR-13 | Automation | The daily run completes without manual intervention on the success path | consecutive scheduled runs |
| NFR-14 | Setup | One-command local setup from a clean clone | fresh-clone test in CI or documented manual check |
| NFR-15 | Cloud deployment | Repeatable deployment through Terraform. No console-created resources | `terraform plan` shows no drift |
| NFR-16 | Recovery | Demonstrated recovery from a simulated failed pipeline (rerun from checkpoint without duplicates) and from loss of the warehouse (rebuild from raw) | recorded recovery drill |
| NFR-17 | Security | No secrets in the repository; least-privilege IAM; dependency scanning in CI | secret scan + dependency scan in CI |
| NFR-18 | Cost | AWS budget alert configured before other resources; dev environment kept within a monthly budget set by the owner | AWS Budgets |
| NFR-19 | Code quality | Ruff and mypy `--strict` clean; SQLFluff clean | CI |
| NFR-20 | Test coverage | Every financial invariant and posting rule is covered. Line-coverage percentage is reported but not used as a gate | CI report |
