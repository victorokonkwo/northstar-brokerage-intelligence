## What and why

<!-- What changed and why. Link roadmap items and requirement/KPI IDs (e.g., FR-M2-04, KPI-02). -->

## Financial impact

<!-- Does this touch money, the ledger, positions, or a KPI? Which invariants (INV-xx) apply? Write "None" if not applicable. -->

## Test evidence

<!-- Paste the commands you ran and their results. If something could not be run, say so and why. -->

```
$ <command>
<result>
```

## Checklist

- [ ] No placeholder or fake logic, and no hardcoded results (CONTRIBUTING.md, W1)
- [ ] New or changed behavior has tests that fail without this change
- [ ] Money uses `Decimal`/`numeric` with explicit currency, and journals balance per currency
- [ ] No silent data substitution (missing/stale prices, FX, fees)
- [ ] Policy values come from versioned config, not constants
- [ ] Docs updated (KPI dictionary, domain model, roadmap status, ADR if a decision changed)
- [ ] Any number quoted is reproducible (command, seed, commit, date)
- [ ] No secrets, real data, or large generated files
