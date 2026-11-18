# Contributing

This is a solo portfolio project run as if it were a team codebase. This file holds the **working rules** (W1–W6), the **definition of done**, and the workflow. The coding standards are in [`docs/engineering/engineering-standards.md`](docs/engineering/engineering-standards.md).

## Working rules

These rules are mandatory for every change, by a person or an AI agent. They are referenced elsewhere as W1–W6.

### W1. No fake implementation, no invented facts

1. **Never write code that pretends to work.** That rules out functions that return hardcoded or sample values in place of real logic, `pass` or `...` bodies, mock results shown as real output, and `try/except` blocks that swallow errors so a demo "succeeds".
2. **Don't scaffold the future.** Don't create empty modules, placeholder files, or directories for later phases. Create a file when it holds real, tested code. The exception is a tool's own standard layout, such as `dbt init` output, kept with `.gitkeep`.
3. If an interface has to exist before its implementation, raise `NotImplementedError("<roadmap item>")`. Never call it from a code path that is presented as working.
4. **Tests must be able to fail.** Don't write tests that assert nothing, that only check a mock returned what it was told to return, or that are skipped without a linked reason.
5. **Never invent numbers.** Performance figures, accuracy, match rates, and benchmark results may appear in docs, READMEs, or commit messages only if a committed, reproducible command produced them. Record the command, data seed and size, commit SHA, and date next to the number. Label targets as targets and illustrative examples as illustrative.
6. **Never invent external facts.** Regulatory rules, settlement conventions, holiday calendars, API endpoints, and library APIs must come from a primary source or from the installed library itself. Record them in [`docs/sources.md`](docs/sources.md). If a fact is unverified, say so in the text.
7. **Report outcomes faithfully.** If tests were not run, were skipped, or failed, say so. Don't describe partially working code as done.

### W2. Financial correctness

1. **The ledger is the system of record** ([ADR 002](docs/adr/002-ledger-system-of-record.md)). Balances, P&L, and cash figures are derived from journal lines. Dashboards and APIs never recompute them independently.
2. **Money is fixed-precision** ([ADR 004](docs/adr/004-fixed-precision-money.md)):
   - Python uses `decimal.Decimal`, and every amount carries an explicit currency. Constructing money from a `float` is an error.
   - SQL uses `numeric(p, s)` / `decimal(p, s)`, never `float`, `real`, or `double precision`, for amounts, prices, quantities, or rates.
   - Rounding happens only at the points ADR 004 defines, using the rounding mode it defines.
   - Arithmetic across currencies without an explicit, dated FX rate is an error.
3. **Every journal batch balances per currency.** Σ debits = Σ credits within each `(journal_batch_id, currency)`. An unbalanced batch is rejected or quarantined. It is never silently dropped.
4. **Ledger entries are immutable.** A mistake is corrected with a reversing entry plus a new entry. Never `UPDATE` or `DELETE` a posted line.
5. **Execution is not settlement.** Trade-date obligations and settlement-date cash/securities movements are separate records with separate states.
6. **Never silently substitute data.** A missing or stale price, FX rate, or fee is flagged and surfaced. It is never filled forward, defaulted to zero, or replaced with a "reasonable" value unless a documented, versioned policy says so and the output records that the policy applied.
7. **Policy values live in versioned configuration**, never in hardcoded constants. That covers margin rates, haircuts, risk limits, fee schedules, and tolerance thresholds. Every result records the policy version that produced it.

### W3. Time and data

1. Timestamps are timezone-aware and stored in UTC. **Naive datetimes are a bug.**
2. The business date is derived in `America/Toronto` under the rules in domain model §5. Settlement dates come from the settlement calendar, never from `date + timedelta(days=1)`.
3. Raw data is append-only. Corrections arrive as new records ([ADR 006](docs/adr/006-bitemporal-records-and-restatements.md)). A published snapshot is never rewritten; a restatement creates a new version that records its reason.
4. Ingestion is idempotent. Re-ingesting the same input yields the same warehouse state, with no duplicate executions.

### W4. Determinism and reproducibility

1. Randomness comes from an explicitly passed `numpy.random.Generator` built from a recorded seed. No global `random`/`np.random` state, and no wall-clock-dependent output.
2. Identifiers are deterministic (domain model §4), so a replay produces identical IDs.
3. Every result that gets persisted records what produced it: `run_id`, code version, input data version, policy/model version, and as-of date.

### W5. Architecture boundaries

See [`docs/architecture/system-overview.md`](docs/architecture/system-overview.md) and [ADR 001](docs/adr/001-platform-architecture.md).

- `src/northstar/domain/` holds pure business logic: no I/O, no database, no network, no environment reads.
- `src/northstar/application/` orchestrates use cases. `src/northstar/infrastructure/` holds adapters for databases, S3, and HTTP.
- `warehouse/dbt/` owns warehouse transformations.
- **Every calculation has exactly one owner**, either Python or SQL, and that owner is recorded in the KPI dictionary ([ADR 007](docs/adr/007-single-owner-for-each-calculation.md)). Don't duplicate a calculation in the other language.
- Dashboards and APIs only read published results. They contain no business calculations.
- Notebooks are for exploration only. Production code never imports from a notebook.
- It is a modular monolith. Don't split it into microservices unless an ADR justifies it.

### W6. Security and data handling

- No secrets in Git: no credentials, tokens, `.env` files, or Terraform state. Use environment variables locally and AWS Secrets Manager in the cloud.
- No real personal or customer data, ever, including in fixtures, screenshots, and logs.
- Large generated datasets live outside Git (see `data/` rules in system overview §6).
- Before you create any AWS resource, confirm that budget alerts exist and that the resource is defined in Terraform.

## Definition of done

A change is done only when **all** of the following are true:

- [ ] The code is real. Nothing in it violates W1.
- [ ] New or changed behavior has tests that fail without the change. Financial logic has invariant tests (W2).
- [ ] Lint, type checks, and tests pass locally, and you ran them yourself. Report the command and the result.
- [ ] Docs that describe the changed behavior are updated: the KPI dictionary, domain model, roadmap status, and a new ADR if a decision changed.
- [ ] Any number quoted in docs is reproducible (W1, rule 5).
- [ ] No secrets, real data, or large generated files are included.

## Workflow

1. **Start from the roadmap.** Each change maps to a roadmap item in [`docs/project/roadmap.md`](docs/project/roadmap.md).
2. **Design first when it matters.** Changes to the data model, the ledger, money handling, or any cross-module contract need a short design note in the PR description. If a decision changes, add an ADR ([`docs/adr/template.md`](docs/adr/template.md)).
3. **Branch** from `main`, named `<type>/<short-description>`, for example `feat/journal-posting`, `fix/settlement-calendar`, or `docs/kpi-dictionary`.
4. **Keep pull requests small.** One logical change per PR. Refactors go in separate PRs from behavior changes.
5. **Write commits** in [Conventional Commits](https://www.conventionalcommits.org/) style: `feat(accounting): post commission lines`.
6. **Self-review** against the checklist in `.github/pull_request_template.md` before merging. Even solo, record the evidence (commands run and their results).

## Review standards

The reviewer, including a self-reviewer, checks in this order:

1. **Correctness:** Do the financial invariants hold? Are edge cases covered, such as partial fills, zero quantities, currency mismatches, holidays, and late data?
2. **Tests:** Would the tests fail if the logic were wrong? Are invariants checked with property tests where that's practical?
3. **Honesty:** Is there any placeholder logic? Are any numbers quoted without a reproducible source?
4. **Design:** Does the change respect the layer boundaries and the single-owner rule for calculations?
5. **Readability:** Are names clear, functions small, and docstrings explicit about financial meaning?

## Documentation changes

Docs are code. Keep them accurate to what exists, and mark anything planned as planned. When you change behavior, update the related docs in the same PR.
