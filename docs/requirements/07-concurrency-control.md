# NFR7 - Concurrency control

## Objective

Prevent lost updates, overselling, invalid state transitions, and
negative counters while choosing the least expensive correct mechanism
for each data shape.

## Final policy

| Surface | Mechanism | Rationale |
|---|---|---|
| `StockItem` inventory updates | Pessimistic `SELECT ... FOR UPDATE` inside `transaction.atomic()` | Inventory is a high-contention invariant. Serializing the short critical section avoids retry storms and overselling. |
| `Order` status transitions | Pessimistic row lock | Foreground requests and background/webhook work can converge on the same state machine. |
| `PaymentIntent` transitions | Pessimistic row lock | Financial state transitions require guarded single-writer behavior and idempotency. |
| `Product` price and metadata | Optimistic version CAS | Admin writes are infrequent, so conflicts are exceptional and should be surfaced clearly. |
| `Customer.loyalty_points` | Atomic SQL F-expression update | Loyalty points are a pure counter; a conditional single-statement update is cheaper than locking or CAS retries. |

Existing inventory, order, and payment locking must not be replaced.

## Pessimistic inventory rule

Every `StockItem` read-modify-write operation must:

1. Enter `transaction.atomic()`.
2. Read the row using `select_for_update()`.
3. Validate availability or state while holding the lock.
4. Update the stock row and insert its `StockMovement` in the same
   transaction.

Multi-row inventory operations acquire locks in ascending product ID
order to avoid circular-wait deadlocks.

## Optimistic Product rule

Product price and metadata updates use:

```text
UPDATE product
SET version = version + 1, ...
WHERE id = :id AND version = :expected_version
```

`core.concurrency.locks.bump_version` raises `StaleObjectError` when the
update affects zero rows. The product price API converts this conflict to
HTTP 409 with code `stale_product_version`.

Human/admin optimistic conflicts must be returned as HTTP 409. They must
not be automatically retried because retrying could overwrite a newer
human decision without review.

## Atomic loyalty counter rule

`adjust_loyalty_points` uses an F-expression to update
`loyalty_points` and `version` in one SQL statement. Deductions include a
`loyalty_points >= amount` predicate so concurrent requests can never
make the balance negative.

This intentionally replaces optimistic CAS for loyalty points. CAS adds
a read and conflict retry loop without improving correctness for a pure
commutative counter update.

## Shared helpers

- `bump_version(model_cls, pk, expected_version, fields)` performs
  optimistic CAS and raises `StaleObjectError` on conflict.
- `select_for_update_or_skip(queryset)` is for queue-style processing
  where locked work should be skipped rather than waited on.
- `with_optimistic_retry(...)` is available for internal operations
  where automatic retry is semantically safe. It is not used for
  human/admin Product edits.

## Prohibited patterns

- Calling `select_for_update()` outside `transaction.atomic()`.
- Replacing inventory, order, or payment locks with uncoordinated writes.
- Performing Python-level read-modify-write arithmetic on loyalty points.
- Automatically retrying stale human/admin Product edits.
- Mixing pessimistic and optimistic locking for the same production
  mutation.

## Verification

- `tests/unit/test_nfr7_pessimistic_inventory.py` proves 50 concurrent
  requests cannot oversell ten units.
- `tests/unit/test_nfr7_optimistic_product.py` proves one expected
  version has exactly one winning Product update.
- `tests/unit/test_nfr7_atomic_loyalty.py` proves increments are not lost
  and deductions cannot make points negative.
- `tools/benchmarks/nfr7_locking_benchmark.py` compares the unsafe
  baseline, production pessimistic reservation, and benchmark-only
  optimistic CAS reservation.
