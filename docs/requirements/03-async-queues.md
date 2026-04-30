# NFR3 — Asynchronous queues

> Owner: **Dev 3**
> Status: stubs ready in `tasks/notifications.py`, `tasks/invoicing.py`;
> Celery already wired in `config/celery.py`.

## Objective

Move every operation the user does **not** need to wait on out of the
request path. The user gets HTTP 201 the moment the order is persisted;
the email and PDF go out of band.

## Tasks to be implemented

| Task | Trigger | Queue priority |
|---|---|---|
| `send_order_confirmation` | `place_order` succeeds | low |
| `send_low_stock_alert` | inventory crosses `reorder_threshold` | low |
| `generate_invoice` | `place_order` succeeds | medium |
| `regenerate_failed_invoices` | beat sweep | low |

(`warm_product_cache` is also a Celery task but belongs to NFR6.)

## Dispatch contract

> Tasks are queued from inside a transaction via
> `transaction.on_commit(lambda: task.delay(args))`.

Why this matters:
- `task.delay(...)` inside the transaction will fire even if the
  transaction rolls back, leaving an orphan task that operates on a row
  that was never committed (or worse, was rolled back to a different
  state).
- `on_commit` defers the dispatch until commit succeeds, eliminating the
  race.

The `core.transactions.on_commit` helper (NFR8) wraps Django's primitive
with logging — once it lands, all tasks should go through it.

## Idempotency

Celery may retry a task under transient failures (`acks_late=True` is set
project-wide). Tasks MUST therefore be **idempotent**:

- `send_order_confirmation(order_id)` — keep an `OrderEmailDispatch`
  table or a Redis SETNX guard so the same order does not produce two
  emails.
- `generate_invoice(order_id)` — check whether the order already has a
  recorded invoice URL before regenerating.

## Retry policy

Stub already configured per-task:

```python
autoretry_for=(Exception,)
retry_backoff=True
retry_kwargs={"max_retries": 5}
acks_late=True
```

The owner should narrow `autoretry_for` to *transient* exceptions only
(network, gateway 5xx) — programming errors should NOT be retried.

## Why Celery + Redis

- Celery is mature on Django and integrates cleanly with `on_commit`.
- Redis (already in the stack for cache and locks) doubles as a broker —
  no new infra dependency.
- Flower exposes per-task latency and failure rates for the demo.

## Acceptance criteria

1. The checkout request returns **before** the email or PDF work happens.
   Latency comparison (NFR10): `place_order` end-to-end p95 with sync
   email vs. async email.
2. Killing `celery_worker` mid-job and restarting it still produces
   exactly one invoice and one email per order (idempotency proof).
3. Flower shows the task lineage with retries on injected failures.

## Files to ship

- `tasks/notifications.py`, `tasks/invoicing.py` — full implementations.
- `apps/orders/services.py::place_order` — replace TODO comments with
  `transaction.on_commit(lambda: ...delay(...))`.
- `docs/benchmarks/nfr3-async-vs-sync.md` with the latency comparison.
