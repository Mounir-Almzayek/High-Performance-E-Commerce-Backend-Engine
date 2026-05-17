# NFR3 - Asynchronous Queues: Implementation Report

> Branch: `feature/orders-celery-integration`
> Status: implemented, ready for review with demo verification.

This report explains the asynchronous queue implementation, why Celery
was used, and why moving invoice/email work out of the request path is
the best design for checkout latency and reliability.

---

## 1. Scope of work

NFR3 moves user-invisible side effects away from synchronous checkout
and into Celery tasks.

| File | What changed |
|---|---|
| `apps/tasks/notifications.py` | Implemented order confirmation email, low-stock alert, and cache warming task stubs |
| `apps/tasks/invoicing.py` | Implemented invoice generation and missing-invoice regeneration |
| `apps/tasks/__init__.py` | Keeps task package and beat schedule wiring |
| `apps/orders/services.py` | Queues invoice and confirmation tasks with `transaction.on_commit(...)` |
| `apps/orders/models.py` | Added `invoice_url` and `OrderEmailDispatch` idempotency state |
| `apps/orders/migrations/0001_initial.py` | Persists the invoice and email-dispatch schema |
| `config/celery.py` | Points Celery discovery at the application task package |

---

## 2. The problem

Checkout should finish when the order is safely persisted, not when
every secondary side effect finishes.

Email delivery and invoice rendering can be slow because they depend on
external systems or expensive PDF work. If those operations run inside
the request, the customer waits longer, p95 latency grows, and transient
provider failures can make a valid checkout look failed.

NFR3 separates the critical path from follow-up work.

---

## 3. Chosen solution

The implementation uses Celery tasks backed by Redis:

| Task | Purpose | Reliability rule |
|---|---|---|
| `tasks.notifications.send_order_confirmation` | Sends confirmation after checkout | Guarded by `OrderEmailDispatch` |
| `tasks.notifications.send_low_stock_alert` | Notifies procurement asynchronously | Fire-and-forget alert task |
| `tasks.invoicing.generate_invoice` | Renders/persists invoice URL | Skips if `invoice_url` already exists |
| `tasks.invoicing.regenerate_failed_invoices` | Sweeps orders missing invoice URLs | Re-queues invoice generation |

Checkout queues the main post-order work like this:

```python
transaction.on_commit(lambda: invoicing.generate_invoice.delay(order.id))
transaction.on_commit(lambda: notifications.send_order_confirmation.delay(order.id))
```

The task arguments are IDs, not model instances, so workers always load
fresh database state.

---

## 4. Why this was the best choice

This solution was best because it optimizes for the user-visible SLA:
checkout response time.

### 4.1 It removes slow work from the checkout path

The order transaction handles the business-critical work: cart lock,
order creation, inventory reservation, and cart close-out. Email and PDF
work happen after commit in Celery. That means a slow SMTP provider or
PDF render cannot add seconds to the checkout response.

### 4.2 It prevents orphan tasks

The use of `transaction.on_commit(...)` is the key correctness decision.
If a task were queued before commit, a rollback could leave a worker
trying to email or invoice an order that does not exist. Deferring
dispatch until commit succeeds makes the queue consistent with the
database.

### 4.3 It accepts at-least-once delivery safely

Celery can retry or redeliver work. Instead of pretending tasks run
exactly once, the implementation makes the important tasks idempotent:

- confirmation email uses `OrderEmailDispatch`
- invoice generation exits early if `invoice_url` already exists
- invoice persistence uses a conditional update for concurrent safety

That is better than relying on timing, because retry-safe design keeps
working after worker restarts and transient failures.

### 4.4 It reuses existing infrastructure

Redis is already part of the project stack for caching and coordination.
Using Redis as the Celery broker avoids adding a new queueing system and
keeps the demo simple. Celery also gives task retry policy, task naming,
worker concurrency, and Flower observability without custom queue code.

### 4.5 It gives separate failure domains

A checkout can succeed even if the email provider is temporarily down.
The failed side effect is retried in the background instead of forcing
the customer to retry the entire order.

---

## 5. Important implementation decisions

### 5.1 Narrow retry policies

`send_order_confirmation` retries SMTP/connection failures, and
`generate_invoice` retries connection failures. This is better than
retrying every `Exception`, because programming errors should surface
quickly instead of being retried repeatedly.

### 5.2 Idempotency table for email

`OrderEmailDispatch` stores whether a confirmation email is pending,
sent, or failed. This gives a durable record and makes duplicate sends
detectable.

### 5.3 Invoice URL as the idempotency guard

The invoice task checks `order.invoice_url` before doing expensive work.
It then writes the URL with a conditional update so a concurrent retry
does not overwrite or duplicate the result.

### 5.4 Beat sweep for failed invoices

`regenerate_failed_invoices` scans orders without `invoice_url` and
queues invoice generation again. This creates a repair path for partial
failures without manual database intervention.

---

## 6. Async Execution vs. Async Queue

The instructor's distinction matters:

| Option | What it means | Best for | Cost |
|---|---|---|---|
| Async execution | Start work outside the current call path, such as a thread, coroutine, or fire-and-forget task | Low-value work that can be lost or recomputed, such as lightweight logs | Simple, but weak durability and retry guarantees |
| Async queue | Persist a message in a broker, then let workers consume it later | Important side effects such as invoices, emails, retries, and repair jobs | Requires Redis/Celery workers, serialization, idempotency, and monitoring |

For this project, invoices and order confirmations belong in a queue
because losing them is not acceptable. A simple log line might use plain
async execution or synchronous logging because the business impact is
much lower.

Redis + Celery is the right level of complexity here: it is a real
message broker setup, it supports retries and Flower visibility, and it
does not add heavier infrastructure such as Kafka for a small e-commerce
demo.

---

## 7. Demo explanation

The clean demo story is:

1. Place an order and show the HTTP response returning before the email
   and invoice work complete.
2. Show Celery/Flower receiving `generate_invoice` and
   `send_order_confirmation`.
3. Restart the worker during a task and show that retry does not create
   duplicate email/invoice state.
4. Show the persisted `invoice_url` and `OrderEmailDispatch` row.

---

## 8. Notes for review

The low-stock alert task exists, but the inventory trigger should be
verified before demo if that part is required in the live walkthrough.
The main checkout-side async flow is implemented through
`transaction.on_commit(...)`.

---

## 9. Summary

The async queue solution is best because it keeps checkout fast, keeps
side effects reliable, and treats retries as a normal distributed-system
behavior. The user waits only for the order to be committed; Celery
handles everything that can safely happen afterward.
