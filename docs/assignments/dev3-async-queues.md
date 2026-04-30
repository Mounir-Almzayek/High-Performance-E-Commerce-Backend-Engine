# Developer 3 — Async queues (NFR3)

## Your scope

You own everything that runs OFF the request path: emails, invoices,
notifications, and their retry / idempotency story.

You do NOT own the daily sales batch (that's Dev 4); you DO own the
`warm_product_cache` task wiring even though its body is filled in by the
NFR6 owner.

## Files you will write code in

| File | What you'll do |
|---|---|
| `tasks/notifications.py` | Implement `send_order_confirmation`, `send_low_stock_alert` |
| `tasks/invoicing.py` | Implement `generate_invoice`, `regenerate_failed_invoices` |
| `apps/orders/services.py::place_order` | Replace the `# TODO [NFR3]` block with `transaction.on_commit(...)` calls |
| `apps/payments/services.py::capture_payment` | Same: dispatch the invoice/notification on commit |
| New model (suggested): `OrderEmailDispatch` in `apps/orders/models.py` | Idempotency guard for confirmation emails |
| `apps/inventory/services.py` | Add the `send_low_stock_alert.delay(...)` trigger when stock crosses threshold (after the lock is released) |

## Files you will read but not modify

- `docs/requirements/03-async-queues.md` — your spec.
- `config/celery.py` and `tasks/__init__.py` — already wired.
- `core/transactions/atomic.py` — once NFR8 lands, use its `on_commit`
  helper instead of `django.db.transaction.on_commit`.

## Definition of done

- A successful `place_order` returns its HTTP response BEFORE the email
  fires (verifiable via Flower timing vs. response timing).
- Killing `celery_worker` mid-job and restarting it produces exactly one
  invoice per order (idempotency proof).
- All tasks have explicit `autoretry_for` narrowed to transient errors
  (network / 5xx), NOT bare `Exception`.
- The retry policy is documented in the report.

## Tips

- `transaction.on_commit(lambda: task.delay(args))` — DO NOT pass model
  instances; pass IDs. Instances captured in the closure can be stale.
- Use `acks_late=True` (already set in stubs) — combined with idempotency
  this gives at-least-once delivery without dups.
- Flower (http://localhost:5555) is your friend — show it during the demo.

## Demo prep

1. Place an order. Show in the access log that the response returns in
   under 100 ms. Show in Flower the email task running afterwards.
2. Restart `celery_worker` mid-task. Show in DB that no duplicate email
   was logged.
