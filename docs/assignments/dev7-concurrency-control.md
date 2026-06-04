# Developer 7 — Concurrency control: optimistic vs. pessimistic (NFR7)

## Your scope

You own the **locking-strategy decision** and its justification. The
`version` field is already on every contended model. Dev 1 owns the
day-to-day correctness of the system under load; you own the deeper
question the examiner cares about: *why* optimistic on one surface and
*why* pessimistic on another, with measured throughput to back it.

The headline deliverable is not code — it's a defensible comparison.
"We chose pessimistic for inventory because optimistic retries thrash on
hot rows during a flash sale" is the sentence you must be able to prove.

## Files you will write code in

| File | What you'll do |
|---|---|
| `core/concurrency/locks.py` | Implement `select_for_update_or_skip`, `bump_version` (CAS, raises `StaleObjectError` on `rowcount=0`), `with_optimistic_retry(fn, retries=3)` with backoff |
| `apps/inventory/services.py` | Stock updates → **pessimistic** (`select_for_update`) |
| `apps/users/services.py` | `adjust_loyalty_points` → **optimistic** (`version` CAS + retry) |
| `apps/products/services.py` | Price / metadata edits → **optimistic** |
| `apps/orders/services.py`, `apps/payments/services.py` | Status transitions → **pessimistic** (correctness > throughput) |
| `tests/unit/test_pessimistic_inventory.py` | 50 concurrent `reserve_stock`, assert no oversell |
| `tests/unit/test_optimistic_loyalty.py` | 50 concurrent `adjust_loyalty_points`, assert exact sum + every loser retried |
| New file: `docs/benchmarks/nfr7-locking.md` | The throughput comparison |

## Files you will read but not modify

- `docs/requirements/07-concurrency-control.md` — your spec (the chosen
  split table is your policy; defend it, don't redesign it without reason).
- `docs/CONCURRENCY_POINTS.md` — the canonical map. Keep it consistent
  with the mechanism you ship for every row.
- `core/transactions/atomic.py` — every lock lives **inside** a
  transaction (Dev 8 owns the wrapper).
- `docs/assignments/dev1-concurrent-access.md` — strong overlap; sync with
  Dev 1 so you don't both touch the same service line.

## Definition of done

- Both helpers are used by **real service code**, not just tested in
  isolation.
- 50 concurrent `reserve_stock` workers → zero oversell, final stock ≥ 0.
- 50 concurrent `adjust_loyalty_points` → exact sum, and the test asserts
  every conflicting writer was retried (not silently dropped).
- `docs/benchmarks/nfr7-locking.md` compares throughput of `reserve_stock`
  under pessimistic vs. optimistic on the **same** workload and defends
  the chosen one.
- `CONCURRENCY_POINTS.md` matches the shipped mechanism for every row.

## Tips

- `select_for_update` outside a transaction is **silently ignored** by
  Django — always wrap it. This is the #1 mistake.
- Optimistic CAS without a retry loop silently drops writes — worse than a
  deadlock. Always retry, always cap.
- Never mix both mechanisms on the same row in the same transaction.
- To benchmark fairly: implement `reserve_stock` **both** ways behind a
  setting flag, run the identical Locust burst against each, compare RPS
  and conflict/retry counts.

## Demo prep

1. Seed one product with `stock = 10`. Fire **100 concurrent** buy
   requests. Assert: exactly 10 succeed, 90 get a clean "out of stock",
   final stock = 0, never negative.
2. Show the throughput table: pessimistic vs. optimistic on `reserve_stock`
   under the same load.
3. Say the sentence: *"Pessimistic for inventory (hot rows, retries
   thrash), optimistic for loyalty (mild contention, cheap writes)."*
