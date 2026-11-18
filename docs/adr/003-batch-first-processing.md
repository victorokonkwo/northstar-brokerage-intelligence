# 003. Batch-first processing

| | |
|---|---|
| Status | Accepted |
| Date | 2026-11-18 |
| Deciders | Project owner |

## Context

The platform's core outputs are daily: reconciled books, end-of-day margin, daily VaR, daily liquidity forecasts, and periodic finance reports. Streaming infrastructure (e.g., Kafka) brings ordering, exactly-once, and replay concerns that would dominate the effort before the batch logic is proven correct.

## Options considered

1. **Event-driven / streaming from the start.** Can show intraday risk. High complexity, and harder to make reproducible.
2. **Batch first.** Daily runs orchestrated by Airflow, keyed by business date, idempotent and replayable. Event-driven updates are an optional later extension.

## Decision

Option 2. All modules run as daily batch jobs keyed by business date. Domain logic is written as pure functions over a snapshot of inputs, so a later event-driven trigger can reuse it unchanged.

## Consequences

- Reproducibility and backfills are straightforward: rerun for a date.
- There is no intraday risk view in the first release. This is stated in the project charter as out of scope.
- If streaming is added later, it must reuse the domain functions rather than reimplement them.

## Revisit when

The daily batch is correct, tested, and published, and there is time left in Phase 8 for an optional intraday risk demo.
