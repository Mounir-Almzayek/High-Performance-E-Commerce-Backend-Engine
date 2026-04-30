# Concurrency points map

Every place in the codebase where two or more concurrent callers can
interfere with each other is listed here, with the protection mechanism
that the owning developer must implement.

This file is the contract between the architecture and the implementers:
if a service function is added/modified in a way that introduces a new
concurrency point, this document MUST be updated in the same change.

---

## 1. Inventory (`apps/inventory/services.py`)

Highest-contention surface in the system. Every public function here is a
race target.

| Function | Race | Mechanism (NFR1 owner) |
|---|---|---|
| `reserve_stock` | Two carts reserving the last unit at the same time | `select_for_update` on `StockItem.id` OR optimistic `version` CAS |
| `release_stock` | Cancellation + automatic timeout reservation expiry overlap | Same as `reserve_stock` |
| `consume_stock` | Webhook-driven capture while the user cancels | Lock + status guard |
| `restock` | Concurrent supplier deliveries / admin imports | Lock-protected increment |
| `bulk_reserve` | Two checkouts share two products in opposite order | **Deterministic lock order** (sort by `product_id` ASC) |

Every successful change must also INSERT a `StockMovement` row in the
SAME transaction (atomicity invariant for NFR8).

---

## 2. Orders (`apps/orders/services.py`)

| Function | Race | Mechanism |
|---|---|---|
| `place_order` | Two tabs from the same user clicking checkout | Lock the `Cart` row first; reuse the same row throughout the transaction |
| `cancel_order` | User cancellation racing with payment-capture webhook | Lock `Order.id`, check status, transition |

Async dispatch (notification, invoicing) MUST go through
`transaction.on_commit` so a rolled-back order never produces a stray
email or PDF.

---

## 3. Payments (`apps/payments/services.py`)

| Function | Race | Mechanism |
|---|---|---|
| `capture_payment` | Duplicate webhook from gateway | UNIQUE index on `external_id` + lock on `PaymentIntent.id` |
| `refund_payment` | Concurrent refund + chargeback | Same locking; status guard |
| `process_webhook` | Same signature delivered to web1 and web2 simultaneously | UNIQUE index on `WebhookEvent.signature` (deduplication) |

---

## 4. Cart (`apps/cart/services.py`)

| Function | Race | Mechanism |
|---|---|---|
| `add_item` | Two clicks within the same second | Lock `Cart.id`; `unique_together(cart, product)` upserts safely |
| `update_item` | User reduces quantity while checkout starts | Cart row lock OR `version` CAS |

---

## 5. Users (`apps/users/services.py`)

| Function | Race | Mechanism |
|---|---|---|
| `register_customer` | Duplicate username/email submission | UNIQUE constraint + `IntegrityError` handling inside `transaction.atomic` |
| `adjust_loyalty_points` | Order completion + admin tweak + refund | Optimistic update on `Customer.version` |

---

## 6. Products (`apps/products/services.py`)

| Function | Race | Mechanism |
|---|---|---|
| `update_product_price` | Two admins editing the same product | Optimistic `version` CAS; on success → `invalidate_product()` (NFR6) |

---

## 7. Cache layer (`core/cache/redis_cache.py`)

| Race | Mechanism |
|---|---|
| Thundering herd on cache miss for a hot key | Single-flight via short-lived Redis lock OR `cache.add()` semantics |
| Cache + DB drift after a write | Writers MUST call `invalidate_product()` after committing |

---

## 8. Lock acquisition order (deadlock avoidance)

Two transactions that lock multiple rows in conflicting orders deadlock.
Convention enforced project-wide:

> **Always acquire locks in ascending primary-key order.**

Concrete rule for `bulk_reserve`: sort `items` by `product_id` before
locking. Same rule for any future multi-row locking (e.g. multi-warehouse
inventory).

---

## 9. Test coverage required

Every row in this file should map to at least one test in `tests/unit/`
that asserts the protection works under simulated concurrency
(`threading` + `transaction.atomic`, or `pytest-django`'s `TransactionTestCase`).

The NFR9 stress test is *not* a substitute — it confirms throughput, but
unit tests confirm correctness of the locks themselves.
