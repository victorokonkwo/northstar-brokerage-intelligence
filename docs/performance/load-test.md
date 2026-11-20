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
(not run yet)
<!-- benchmark:end -->

## Generation scaling

Two scaling defects were found while sizing this test and fixed in Phase 3G:
- **Clock overrun:** with 5–90 second steps, a day's shared clock ran past the 16:30 close publication at about 1,600 accounts, and the engine refused the next action. The step is now configurable (`clock_step`). The generator also refuses a day that overruns, with a message saying so.
- **Quadratic funds checks:** a profile showed every order and fill scanning every client's balance. The engine now indexes balances by account.

The dev dataset's events file is byte-identical after both fixes (SHA-256 `c70ac58c…`).

Generation alone, in memory, one process per size, measured with the Docker stack stopped (`--sweep`, no stack needed):

<!-- sweep:start -->
(not run yet)
<!-- sweep:end -->

## The 1M-execution target (open)

The dev profile generates about 15.4 executions per account in Q1 2026, so 1M executions needs about 65,000 accounts. Extrapolating the measured memory growth gives roughly 10 GB for an in-memory run, more than the reference laptop has. **This is an extrapolation, not a measurement.** Ingestion and the warehouse load also hold a whole file in memory. Phase 8 builds streaming generation and loading (per-day event flushing, chunked ingestion and loading), keeping IDs and hashes identical, and measures 1M there.
