# 013. Corporate events: cash dividends, and option exercise and expiry

| | |
|---|---|
| Status | Accepted |
| Date | 2026-11-20 |
| Deciders | Project owner |

## Context

The Phase 5 plan adds dividends (OQ-05) and option exercise and expiry (the rest of OQ-03) to the scope, as roadmap 5C. Until now the platform assumed that positions change only by trades, and client cash only by deposits, withdrawals, FX, interest, and trades. Every dev option expired after the window.

The facts the design relies on are in [sources.md](../sources.md#corporate-events), read on 2026-11-20:

- **Ex-date.** Under T+1 the ex-dividend date is the record date, in the US (FINRA Rule 11140, from 2024-05-28) and on the TSX (from 2024-05-27).
- **Expiry.** A standard monthly option expires on the third Friday, or on the preceding open day if the exchange is closed (OCC By-Laws).
- **Delivery.** Exercise delivers the underlying on the first business day after exercise (OCC Rule 903 under T+1). This is partly verified: the proposal was read, not the current rulebook.
- **Exercise by exception.** Customer equity options $0.01 or more in the money are exercised automatically. Also partly verified.

## Decision

Owner decisions, 2026-11-20 (the 5C design note):

1. **Five canonical events (schema v1):**

   | Event | Posts | What it records |
   |---|---|---|
   | `dividend_declared` | no | The issuer's declaration: rate per share in whole cents, and the ex, record, and pay dates. The ex-date equals the record date, a business day |
   | `dividend_paid` | yes | One per dividend, on the pay date, with an allocation per entitled account |
   | `option_exercised` | yes | An exercise, early or automatic at expiry |
   | `exercise_settled` | yes | The T+1 delivery of the underlying against the strike amount |
   | `option_expired` | no | An out-of-the-money option lapses |

2. **Entitlement:** an account's settled quantity at the end of the record date. Under T+1 that equals its trade-date quantity at the close of the day before the ex-date.

3. **Dividends are cash basis on the pay date, with no new accounts.**
   - **Posting:** debit 1000 for the total, credit 2100 per account.
   - **Bank match:** the single GL 1000 line matches the depository's single bank line by `dividend_id`.
   - **Rounding:** a whole-cent rate times a whole number of shares is exact, so the allocations need no rounding.

4. **Exercise posts like a trade.** Exercises reuse 1200, 2200, 2100, and 1000:
   - a call debits 2100 and credits 2200, then settles 2200 against 1000
   - a put debits 1200 and credits 2100, then settles 1000 against 1200

   There is no exercise fee. The option leaves the position on the exercise date, and the underlying moves at trade date and settles one business day later on its market's settlement calendar.

5. **Expiry policy** (`config/brokerage/option_expiry_policy.yaml`, versioned, illustrative). On the expiry date:
   - **Close-out:** the brokerage sells at the close any in-the-money long put the account can't deliver shares for, and any in-the-money call the account can't fund: a CASH account without the strike, or a MARGIN account that would borrow over its guard.
   - **Exercise or expire:** after the close, every remaining option $0.01 or more in the money is exercised; the rest expire.

6. **An unexercisable option of a restricted account expires** (owner decision during 5C-b). A trading-restricted account can't place orders, and a dbt control checks that it never does, so the brokerage can't sell its in-the-money option either. The option expires unexercised, and `option_expired` records the reason, `NOT_EXERCISABLE` (otherwise `OUT_OF_THE_MONEY`).
7. **Close-outs are planned in order.** The brokerage decides what to sell by walking the expiry in the order it is processed. An earlier exercise uses up cash or borrowing room, or delivers shares; an earlier close-out sale adds its proceeds less commission. So no exercise fails after the close (found in testing: two calls, each fundable alone but not together).
8. **Reconciliation through the existing record sets.** Bank lines, settlement confirmations, and custody snapshots reflect dividends and exercises. There is no new record set and no new reconciliation.

9. **Market behaviour in the simulator:**
   - an equity drops by the dividend at the ex-date
   - options are priced with the dividends declared by the pricing day
   - a holder may exercise early when the model says exercising beats waiting a day: the tree has one step per calendar day to expiry, since clients decide once a day after the close

## Consequences

- **Dividends add a GL 1000 source.** Cash reconciliation and the bank lines gain dividend and exercise references.
- **Position models add events.** Positions, the stock record, and INV-03/INV-05 now include exercise, settlement of exercise, and expiry.
- **The dataset changes again.** Locally published snapshots restate; CI starts from scratch.
- **No pending dividends.** A client sees nothing between the record and pay dates (cash basis).

## Out of scope

- **Withholding tax.** The verified treaty rate on US dividends to a Canadian resident is 15%; the owner decided not to model it, a † simplification.
- **Other corporate actions:** stock dividends, splits, and others.
- **Assignment and short positions,** since clients are long only.
- **Index or cash-settled options.**
- **Dividend reinvestment.**

## Revisit when

- Registered accounts or withholding are modelled.
- Clients may write options (assignment).
- OCC's current Rules 805 and 903 can be read, to settle the two partly verified facts.
