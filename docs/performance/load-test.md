# Load test

| | |
|---|---|
| Requirements | FR-SIM-06 (generation from a dev seed up to a load-test dataset of at least 1M executions, runtime and memory measured), NFR-08 (generate, ingest, transform, and query at least 1,000,000 executions) |
| Status | **Partly met.** The pipeline is measured end to end at the size below. The 1M-execution target is open: an in-memory run that large does not fit the 8 GB reference laptop, so it moves to Phase 8 with streaming generation and loading (owner decision, 2026-11-19). |
| How to reproduce | `make up`, then `make benchmark` (`scripts/benchmark.py`; several minutes). It writes the section below. |

## What is measured

`scripts/benchmark.py` runs each stage in a fresh process and records the stage's wall time and the peak resident memory of the Python (or dbt) process. Postgres and SeaweedFS run in Docker and are **not** included in the memory figures. The `*_load` schemas are dropped first, so the warehouse starts empty. Raw objects are content-addressed, so a re-run on the same machine writes no new raw objects, and its ingest is a replay.

The dataset is `config/simulation/datasets/load.yaml`: the dev profile (Q1 2026, the same instruments and activity) with more accounts, and millisecond clock steps so a day's actions fit before the 16:30 close.

## Results

<!-- benchmark:start -->
### Run of 2026-11-20 01:20 UTC

| | |
|---|---|
| Command | `uv run python scripts/benchmark.py --dataset config/simulation/datasets/load.yaml` |
| Dataset, seed | `config/simulation/datasets/load.yaml`, seed 42, defects `config/simulation/defects/dev.yaml` |
| Code version | d05f7e7bacd34e64c1c5a6b6e39141bcba41699b |
| Machine | arm64, 8 CPUs, Darwin 27.0.0 |
| Python | 3.13.18 |

| Stage | Wall time (s) | Peak memory (MiB) | Result |
|---|---:|---:|---|
| simulate | 116.2 | 1112 | ok: dataset load seed 42: 559694 events under data/generated/load/seed=42 / event trade_executed 98334 |
| ingest | 41.6 | 1401 | ok: run bench-20261120T011212Z-a9b2d9 SUCCEEDED: 2028956 accepted, 0 duplicate, 0 quarantined lines; 1158 raw objects (0 new) |
| load | 186.4 | 1192 | ok: run bench-20261120T011212Z-a9b2d9: read 559694 events and 1469262 external records, posted 495028 batches; loaded into raw_load/ledger_load: 559694 new events, 1300752 new journal lines, 1469262 new external records |
| dbt build | 125.9 | 152 | ok: 01:20:03  Done. PASS=174 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=174 |
| publish | 31.4 | 101 | ok: run publish-12075b9fa446: 63 business dates; published 63 |

Total wall time: 501.5 s.
<!-- benchmark:end -->

**Reading the run.**
- **Size:** the load dataset (6,400 accounts, Q1 2026) has 559,694 events and 98,334 executions. Its external records and the posted journal bring it to 2,028,956 ingested lines, 1,300,752 journal lines, and 1,469,262 external records in the warehouse. All 174 dbt nodes pass, and every one of the 63 reporting dates is published.
- **Ingest was a replay:** it wrote 0 new raw objects, because an earlier trial run on this machine had already stored identical content-addressed objects. It still validated every line and offered every object with put-if-absent.
- **Where the time goes:** the warehouse load is the slowest stage (186 s): Python posts 495,028 journal batches, and everything is loaded in one transaction. Ingest has the largest Python memory peak (1,401 MiB), because it holds the whole input in memory. Both are the stages Phase 8's streaming work targets.

## Generation scaling

Two scaling defects were found while sizing this test and fixed in Phase 3G:
- **Clock overrun:** with 5–90 second steps, a day's shared clock ran past the 16:30 close publication at about 1,600 accounts, and the engine refused the next action. The step is now configurable (`clock_step`). The generator also refuses a day that overruns, with a message saying so.
- **Quadratic funds checks:** a profile showed every order and fill scanning every client's balance. The engine now indexes balances by account.

The dev dataset's events file is byte-identical after both fixes (SHA-256 `c70ac58c…`).

Generation alone, in memory, one process per size, measured with the Docker stack stopped (`--sweep`, no stack needed):

<!-- sweep:start -->
### Generation sweep of 2026-11-20 01:11 UTC

| | |
|---|---|
| Command | `uv run python scripts/benchmark.py --dataset config/simulation/datasets/load.yaml --sweep 3200 6400 12800` |
| Dataset, seed | `config/simulation/datasets/load.yaml`, seed 42 |
| Code version | d05f7e7bacd34e64c1c5a6b6e39141bcba41699b |
| Machine | arm64, 8 CPUs, Darwin 27.0.0 |
| Python | 3.13.18 |

| Accounts | Events | Executions | Wall time (s) | Peak memory (MiB) |
|---:|---:|---:|---:|---:|
| 3,200 | 278,450 | 49,178 | 28.8 | 859 |
| 6,400 | 559,694 | 98,334 | 59.0 | 1103 |
| 12,800 | 1,079,700 | 189,734 | 126.9 | 1600 |
<!-- sweep:end -->

## The 1M-execution target (open)

The dev profile generates about 15 executions per account in Q1 2026 (189,734 for 12,800 accounts in the sweep), so 1M executions needs about 67,000 accounts. Scaling the largest measured generation linearly (1,600 MiB at 189,734 executions) gives about 8.2 GiB for generation alone, before ingestion holds the files. That is more than the reference laptop's 8 GB, which also runs the Docker stack. **This is an extrapolation, not a measurement.** Ingestion and the warehouse load also hold a whole file in memory. Phase 8 builds streaming generation and loading (per-day event flushing, chunked ingestion and loading), keeping IDs and hashes identical, and measures 1M there.
