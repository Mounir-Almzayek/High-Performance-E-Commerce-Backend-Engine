# NFR8 — ACID transactions

> Owner: _unassigned_ — stub-ready in `core/transactions/atomic.py`.

## Objective

Guarantee that **composite operations** (charge payment + decrement stock
+ create order) either commit fully or roll back fully, even under
concurrent access.

## Composite operations in scope

| Operation | Files |
|---|---|
| Place an order | `apps/orders/services.py::place_order` |
| Capture a payment | `apps/payments/services.py::capture_payment` |
| Cancel an order | `apps/orders/services.py::cancel_order` |
| Refund a payment | `apps/payments/services.py::refund_payment` |
| Daily aggregation persistence | `tasks/daily_sales_batch.py` |

Each composes ≥ 2 writes that must succeed atomically.

## Mechanism

Three primitives in `core/transactions/atomic.py`:

### `atomic_with_isolation(level="read committed")`

A context manager that:
1. Wraps the body in `transaction.atomic()`.
2. Issues `SET TRANSACTION ISOLATION LEVEL ...` so the choice is
   explicit and visible in code review.

Default is `READ COMMITTED` (Postgres default). Reasons to escalate:
- `REPEATABLE READ` for read-modify-write patterns when the caller
  cannot use `SELECT ... FOR UPDATE`.
- `SERIALIZABLE` only with an explicit retry loop — Postgres returns
  `serialization_failure` and expects the app to retry.

### `on_commit(callback)`

Thin wrapper over `django.db.transaction.on_commit` with structured
logging so deferred callbacks are visible in NFR10 traces. Used to
defer:
- Celery task dispatch (NFR3).
- Cache invalidation (NFR6).

### `run_saga(steps)`

For workflows that touch external systems (payment gateway), 2PC is not
available. A linear saga executes `steps[0].action ... steps[N].action`,
and on failure walks back the *completed* steps in reverse, calling each
step's `compensation`.

Compensation requirements (must be documented per saga):
- **Idempotent**: running the same compensation twice yields the same
  effect as running it once.
- **Safe under retries**: compensations themselves can fail, so the
  saga runner persists progress and retries.

## Atomicity invariants enforced project-wide

For each composite operation, the report MUST list every write involved
and confirm they are inside the same `atomic_with_isolation` block:

```
place_order:
  - INSERT Order
  - INSERT OrderItems (bulk)
  - UPDATE StockItem (per item)
  - INSERT StockMovement (per item)
  - UPDATE Cart (status)
  - on_commit: dispatch Celery tasks    [<-- AFTER commit, NOT inside]
```

## Why explicit isolation levels

The default is fine 95 % of the time, but a single ill-placed
`REPEATABLE READ` can convert a benign race into a `serialization_failure`
under load. Forcing the developer to write the level explicitly makes
the choice visible during review, and the structured log makes it
visible at runtime.

## Failure injection

The acceptance criteria require proof that rollback works. Suggested
testing approach:

```python
def test_place_order_rollback_on_payment_init_failure(monkeypatch):
    # arrange: mock payments.services.create_intent to raise
    # act: call place_order
    # assert: no Order, no StockMovement, stock unchanged
```

## Acceptance criteria

1. Each composite operation has a unit test that injects a failure
   mid-flow and asserts ZERO partial state remains.
2. The Locust soak run (NFR9) with random failure injection produces no
   inconsistent state (verified by an end-of-run integrity audit query).
3. The NFR8 report documents the chosen isolation level for each
   operation and the reason.

## Files to ship

- `core/transactions/atomic.py` — full implementations.
- `tests/unit/test_atomic_*.py` — failure-injection tests.
- `tools/integrity_audit.sql` — query that asserts inventory and orders
  are consistent after a run.
