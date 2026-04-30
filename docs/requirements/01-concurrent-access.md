# NFR1 — Concurrent access and data integrity

> Owner: **Dev 1**
> Status: stubs ready in `core/concurrency/locks.py` and every
> `apps/*/services.py`.

## Objective

Allow many users to mutate shared resources (most importantly stock
levels) without producing **race conditions**, lost updates, or
inconsistent reads.

The deliverable is the proof — under simulated concurrency — that two
buyers competing for the last unit of a product cannot both succeed.

## Scope

Hot spots covered by this requirement:

- `apps.inventory.services.reserve_stock / release_stock / consume_stock /
  bulk_reserve / restock`
- `apps.orders.services.place_order / cancel_order`
- `apps.payments.services.capture_payment / refund_payment / process_webhook`
- `apps.cart.services.add_item / update_item`
- `apps.users.services.adjust_loyalty_points`

A complete map with the specific race per function is in
[../CONCURRENCY_POINTS.md](../CONCURRENCY_POINTS.md).

## Approach

Three primitives, layered:

1. **Database row locks** (`SELECT ... FOR UPDATE`) on the canonical row
   for the operation. This is the default for inventory and orders because
   the contention point IS the database row.
2. **Optimistic version CAS** for low-contention metadata
   (`Customer.loyalty_points`, `Product.price`).
3. **Distributed Redis locks** for resources that have no single canonical
   row — for instance, "give me the right to send exactly one
   confirmation email per order across web1 and web2".

All three live in `core/concurrency/locks.py` so the choice is documented
and reusable.

## Deadlock avoidance rule

When more than one row is locked in the same transaction:

> Acquire locks in **ascending primary key order**.

Concrete example: `bulk_reserve(items=[(7, 1), (3, 2)])` must lock product
3 before product 7, regardless of the input order.

## Why each tool was chosen (engineering rationale)

- **`select_for_update` over Redis locks** for inventory: the data is in
  Postgres, so combining the lock with the write inside one transaction
  is atomic by construction. Adding a Redis lock would create a second
  source of truth and introduce its own failure modes.
- **`select_for_update(skip_locked=True)`** on hot rows when fairness
  matters less than throughput (e.g. webhook processing). Without
  `skip_locked`, a slow caller blocks every other caller behind it.
- **Optimistic locking** for loyalty points / product price: contention is
  rare, so paying the cost of a row lock per call is wasteful. CAS retries
  are cheaper in the common case.
- **Redis distributed lock** is reserved for the few cross-instance
  coordination needs (cache warmer election, "send-once" tasks). The
  implementation uses `SET NX PX` and a Lua compare-and-delete release to
  avoid lifting a lock owned by another holder.

## Acceptance criteria

1. Two parallel `place_order` calls competing for one unit of stock
   produce **exactly one success** and one `NotEnoughStock`.
2. Two parallel `capture_payment` calls with the same `external_id`
   produce **exactly one** state transition.
3. A unit test (`tests/unit/test_concurrency_*.py`) demonstrates each
   acceptance case using `threading` + `TransactionTestCase`.
4. The Locust mixed scenario (NFR9) reports zero
   `IntegrityError` / oversold inventory across a 10-minute run.

## Tests to add

```
tests/unit/test_concurrency_inventory.py
tests/unit/test_concurrency_orders.py
tests/unit/test_concurrency_payments.py
```

Each test follows the same template:

```python
def test_two_workers_one_unit_of_stock(transactional_db):
    # arrange one product with on_hand=1
    # spawn two threads each calling reserve_stock
    # assert: exactly one returns OK, one raises NotEnoughStock
    ...
```
