# Developer 1 — Concurrent access (NFR1)

## Your scope

You own protecting every shared resource against race conditions. You
will *not* finish every concurrency-control bell and whistle (NFR7 owns
the optimistic vs. pessimistic comparison), but you DO own the day-to-day
correctness of the system under concurrent traffic.

## Files you will write code in

| File | What you'll do |
|---|---|
| `core/concurrency/locks.py` | Implement `distributed_lock`, plus a thin compare-and-set helper |
| `apps/inventory/services.py` | Implement all 5 functions (lock + transaction + StockMovement) |
| `apps/orders/services.py::place_order` | Replace the inventory-reserve TODO; complete the locking on the Cart row |
| `apps/orders/services.py::cancel_order` | Implement |
| `apps/payments/services.py::capture_payment / refund_payment / process_webhook` | Implement |
| `apps/cart/services.py::update_item` | Implement; review `add_item` |
| `apps/users/services.py::adjust_loyalty_points` | Implement |

## Files you will read but not modify

- `docs/requirements/01-concurrent-access.md` — your spec.
- `docs/CONCURRENCY_POINTS.md` — the canonical map. Update it when you
  introduce or remove a concurrency point.
- `core/transactions/atomic.py` — once NFR8 owner finishes, use
  `atomic_with_isolation` and `on_commit` from there instead of raw
  Django primitives.

## Definition of done

- All `NotImplementedError` raises in your scope are gone.
- Every public function in `apps/*/services.py` is wrapped in a
  transaction and uses an explicit lock OR an explicit version CAS.
- Unit tests exist for at least:
  - oversold-prevention on `reserve_stock`,
  - duplicate-webhook idempotency on `process_webhook`,
  - place_order rollback when `bulk_reserve` raises.
- The CONCURRENCY_POINTS map is up to date.

## Tips

- Always sort by `product_id` ASC before locking multiple stock rows.
- Use `tests/unit/` and the `transaction=True` fixture from
  `pytest-django` to truly hit the DB (`TransactionTestCase`).
- Combine `threading` with that fixture to simulate concurrency in a
  test.
- Decorate your service functions with `@audit_log("...")` and `@timed("...")`
  so failures during the demo are traceable.

## Demo prep

Bring a script that:
1. Seeds one product with `on_hand=1`.
2. Spawns 10 concurrent `place_order` calls.
3. Asserts exactly one Order with status `pending` and exactly one
   `StockMovement(kind=reserve)` row.
