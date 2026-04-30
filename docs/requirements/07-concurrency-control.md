# NFR7 — Concurrency control: optimistic vs. pessimistic locking

> Owner: _unassigned_ — stub-ready in `core/concurrency/locks.py`.
> The `version` field is already added to every contended model.

## Objective

Apply **optimistic** OR **pessimistic** locking on stock-level updates,
and justify the choice with engineering arguments and measurements.

## Definitions (for the report)

**Pessimistic locking** — acquire an exclusive row lock at the start of
the transaction (`SELECT ... FOR UPDATE`), guaranteeing serial access at
the cost of throughput when contention is low.

**Optimistic locking** — read freely, attempt the write only if the
version field is unchanged since the read. Cheap when contention is rare,
but the application must handle the retry on conflict.

## Project policy (chosen split)

| Surface | Mechanism | Why |
|---|---|---|
| `StockItem` (inventory) | **Pessimistic** (`select_for_update`) | High contention on a small set of hot rows during flash sales — optimistic retries thrash. Postgres lock cost is amortized. |
| `Order` status transitions | Pessimistic | Foreground + webhook converge here; correctness > throughput. |
| `PaymentIntent` | Pessimistic | Same reason as Order. |
| `Customer.loyalty_points` | **Optimistic** (`version` CAS) | Contention is mild and writes are cheap; retries are acceptable. |
| `Product` (price / metadata) | Optimistic | Admin updates are rare; surface latency does not need to absorb a row lock. |

## Helpers to implement

- `select_for_update_or_skip(qs)` — wrapper that always sets
  `skip_locked=True`. Used for queue-style processing where a slow holder
  must not block siblings.
- `bump_version(instance)` — implements the optimistic update:
  `UPDATE ... SET version = version + 1 WHERE pk=... AND version=?`
  raises `StaleObjectError` on `rowcount=0`.
- `with_optimistic_retry(fn, retries=3)` — small helper that reruns `fn`
  on `StaleObjectError`, with exponential backoff and a hard cap.

## What MUST NOT be done

- No `select_for_update` outside of a transaction (Django will silently
  ignore it).
- No optimistic CAS without retries — silently dropping writes is worse
  than a deadlock.
- No mixing both mechanisms on the same row in the same transaction.

## Test requirements

- `tests/unit/test_pessimistic_inventory.py`:
  spawn 50 concurrent `reserve_stock` workers, assert no oversell.
- `tests/unit/test_optimistic_loyalty.py`:
  spawn 50 concurrent `adjust_loyalty_points`, assert sum is exact and
  every loser was retried.

## Acceptance criteria

1. Both helpers (`select_for_update_or_skip`, `bump_version`) are used by
   real service code (not just tested in isolation).
2. The NFR7 report compares throughput of `reserve_stock` under
   pessimistic vs. optimistic implementations on the same workload, and
   defends the chosen one.
3. The CONCURRENCY_POINTS map is fully consistent with the chosen
   mechanism for every row.

## Files to ship

- `core/concurrency/locks.py` — full implementations.
- Call-sites in every `apps/*/services.py` that today raise
  `NotImplementedError`.
- `docs/benchmarks/nfr7-locking.md` with the comparison.
