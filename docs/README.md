# Documentation index

Every document states whether it describes something **implemented** or **planned**. Phase 0 (design) was completed on 2026-11-18. All architecture and module descriptions are still designs: nothing is implemented yet.

## Business

| Document | Purpose |
|---|---|
| [project-charter.md](business/project-charter.md) | Scope, objectives, stakeholders, flagship scenario, out-of-scope items |
| [requirements.md](business/requirements.md) | Functional requirements per module, with acceptance criteria |
| [business-glossary.md](business/business-glossary.md) | Definitions of brokerage, accounting, risk, and forecasting terms |
| [kpi-dictionary.md](business/kpi-dictionary.md) | The first 20 KPIs: formula, grain, owner, source, verification |

## Architecture

| Document | Purpose |
|---|---|
| [system-overview.md](architecture/system-overview.md) | Layers, components, data flow, environments, storage and repository layout |
| [domain-model.md](architecture/domain-model.md) | Canonical entities, identifiers, event envelope, accounting model, financial invariants, time semantics |
| [non-functional-requirements.md](architecture/non-functional-requirements.md) | Accuracy, reproducibility, scale, freshness, security, and operability targets |
| [adr/](adr/) | Architecture decision records |

## Project

| Document | Purpose |
|---|---|
| [roadmap.md](project/roadmap.md) | Phases, exit gates, Sprint 1 plan, **current status** |
| [risk-register.md](project/risk-register.md) | Project, financial-correctness, and model risks, with mitigations |

## Engineering

| Document | Purpose |
|---|---|
| [engineering-standards.md](engineering/engineering-standards.md) | Python, SQL/dbt, testing, configuration, logging, and review standards |

## References

| Document | Purpose |
|---|---|
| [sources.md](sources.md) | External facts the design relies on, with source and verification status |

## Documents to be written when their phase starts

These are listed so nobody assumes they exist: data-governance docs (catalog, lineage, retention, reporting controls), quantitative methodology docs (margin, VaR, liquidity forecasting), model cards, runbooks, and portfolio material (case study, performance results, demo script). Each is written alongside the implementation it describes, never ahead of it.
