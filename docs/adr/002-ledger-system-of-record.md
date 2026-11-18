# 002. Double-entry ledger as the financial system of record

| | |
|---|---|
| Status | Accepted |
| Date | 2026-11-18 |
| Deciders | Project owner |

## Context

Cash balances, client balances, margin loans, revenue, and costs are used by reconciliation, risk, treasury, and finance reporting. If each consumer derives these figures its own way, for example by summing executions in a dashboard, the figures diverge and no single number can be audited.

## Options considered

1. **Derive balances directly from operational events** (executions, deposits) wherever they are needed. Simple at first. Over time there are many definitions, nothing guarantees they balance, and corrections are hard to apply.
2. **Double-entry ledger as the system of record.** Every business event is posted through a versioned posting rule into balanced journal lines. All financial figures derive from those lines.

## Decision

Option 2.

- Every financial event posts a **journal batch** that balances **within each currency**.
- Posted lines are **immutable**. Corrections are reversals plus new postings.
- Balances, P&L, client cash, and margin loans are **derived only from journal lines**.
- Execution (trade date) and settlement are separate postings.
- Posting rules are versioned. Each line records `posting_rule_id` and `posting_rule_version`.
- The chart of accounts and posting rules are in the [domain model §9](../architecture/domain-model.md).

## Consequences

- One auditable source of financial truth. The balance invariant (INV-01) is mechanically testable.
- Adding a new event type requires a posting rule and its tests before it can affect any figure.
- More upfront design. The FX conversion mechanics (OQ-01) must be settled before multi-currency trading is implemented.
- This is a simplified management ledger, not IFRS statements, and is documented as such.

## Revisit when

Reporting needs accounting treatments the simplified chart of accounts cannot represent.
