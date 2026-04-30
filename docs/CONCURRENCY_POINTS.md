# Concurrency points map

Every place in the codebase where two or more concurrent callers can
interfere with each other is listed here, with the protection mechanism
in effect.

This file is the contract between the architecture and the implementers:
if a service function is added/modified in a way that introduces a new
concurrency point, this document MUST be updated in the same change.

> **Status legend:** ✅ implemented (NFR1 branch) · ⏳ pending (other NFR owner)

For the engineering rationale behind each choice see
[reports/01-nfr1-implementation.md](reports/01-nfr1-implementation.md).

---

## 1. Inventory (`apps/inventory/services.py`) ✅

Highest-contention surface in the system. Every public function here is a
race target. All five are protected with `SELECT ... FOR UPDATE` inside
`@transaction.atomic`.

| Function | Race | Implemented mechanism |
|---|---|---|
| `reserve_stock` | Two carts reserving the last unit at the same time | Pessimistic row lock on `StockItem` + F-expression update + StockMovement insert in same atomic |
| `release_stock` | Cancellation + reservation timeout overlap | Same discipline |
| `consume_stock` | Webhook capture racing with foreground cancel | Lock + status guard |
| `restock` | Concurrent supplier deliveries / admin imports | Lock + `on_hand = F('on_hand') + qty` |
| `bulk_reserve` | Two checkouts share two products in opposite order | **PK-ASC lock order**: `select_for_update().filter(id__in=...).order_by("product_id")` |

Every successful change must also INSERT a `StockMovement` row in the
SAME transaction (atomicity invariant for NFR8).

---

## 2. Orders (`apps/orders/services.py`) ✅

| Function | Race | Implemented mechanism |
|---|---|---|
| `place_order` | Two tabs from the same user clicking checkout | `select_for_update` on `Cart`; cart-locked region wraps `bulk_reserve` (PK-ASC) and the Order/OrderItem inserts |
| `cancel_order` | User cancellation racing with payment-capture webhook | `select_for_update` on `Order` + status guard; release_stock called per item in PK-ASC order |

Async dispatch (notification, invoicing) is left to NFR3 owner.
Implementation note in `place_order` comments where the
`transaction.on_commit` calls go.

---

## 3. Payments (`apps/payments/services.py`) ✅

| Function | Race | Implemented mechanism |
|---|---|---|
| `capture_payment` | Duplicate webhook from gateway / parallel "Pay" clicks | `select_for_update` on `PaymentIntent`; same-`external_id` short-circuit returns idempotently; consume_stock per item in PK-ASC order |
| `refund_payment` | Concurrent refund + chargeback | Same locking discipline + status guard |
| `process_webhook` | Same signature on web1 and web2 simultaneously | UNIQUE index on `WebhookEvent.signature`; INSERT in inner `atomic`, `IntegrityError` -> return False (deduplicated) |

---

## 4. Cart (`apps/cart/services.py`) ✅

| Function | Race | Implemented mechanism |
|---|---|---|
| `add_item` | Two clicks within the same second / two tabs | `select_for_update` on `Cart`; `get_or_create` + atomic in-row `quantity` increment under the lock |
| `update_item` | User reduces quantity while checkout starts | `select_for_update` on `Cart`; status guard refuses mutation if cart already CHECKED_OUT |

---

## 5. Users (`apps/users/services.py`) ✅

| Function | Race | Implemented mechanism |
|---|---|---|
| `register_customer` | Duplicate username/email submission | UNIQUE constraint at the auth layer + `transaction.atomic` so a failed Customer create rolls back the User |
| `adjust_loyalty_points` | Order completion + admin tweak + refund | F-expression atomic update — single SQL statement, no application lock; conditional WHERE clause prevents going negative |

---

## 6. Products (`apps/products/services.py`) ⏳

| Function | Race | Mechanism |
|---|---|---|
| `update_product_price` | Two admins editing the same product | Optimistic `version` CAS via `core.concurrency.locks.bump_version`; on success → `invalidate_product()` (NFR6 owner) |

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
