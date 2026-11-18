# 001. Modular monolith in a domain-driven monorepo

| | |
|---|---|
| Status | Accepted |
| Date | 2026-11-18 |
| Deciders | Project owner |

## Context

The platform has five capabilities (data foundation, financial controls, market risk, treasury, finance reporting), and all of them must share one consistent financial data model. It is built and run by one developer. Consistency of definitions, such as accounts, executions, and balances, matters more than independent deployment of modules.

## Options considered

1. **Five separate repositories or projects.** Each module is simple on its own. But data models and calculations get duplicated, definitions drift, and no realistic end-to-end test is possible.
2. **Microservices in one repository.** Modules are independently deployable. The cost is network contracts, deployment, and operational overhead out of proportion to one developer, and integration tests get harder.
3. **Modular monolith in one monorepo.** One Python package with domain-separated subpackages, a dbt project, and independently runnable batch jobs. Delivery interfaces (API, dashboards) deploy separately but import the same domain code.

## Decision

Option 3. One repository, one Python package (`northstar`) with layered subpackages (`domain`, `simulation`, `ingestion`, `infrastructure`, `application`), one dbt project, and separately deployable API and dashboard containers. Layer rules are in [system overview §3](../architecture/system-overview.md).

## Consequences

- A single end-to-end test can exercise the whole flagship scenario.
- Module boundaries are enforced by convention and review rather than by the network. An import-linting check (e.g., `import-linter`) should be added in Phase 1 to make the boundaries testable.
- Deploying one module means building the shared package. That is acceptable at this scale.

## Revisit when

A module needs an independent scaling profile or release cadence that the monolith cannot provide, such as real-time risk.
