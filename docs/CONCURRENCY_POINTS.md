# Concurrency points map

Every place where concurrent callers can interfere is listed here with
the protection mechanism in effect. New or changed concurrency points
must update this document in the same change.

## 1. Inventory (`apps/inventory/services.py`) - implemented

Inventory is the highest-contention surface. All public stock mutations
use `SELECT ... FOR UPDATE` inside `transaction.atomic()`. Every
successful change also inserts a `StockMovement` in the same transaction.

| Function | Race | Implemented mechanism |
|---|---|---|
| `reserve_stock` | Two carts reserve the last unit | Pessimistic row lock on `StockItem`, guarded availability check, F-expression update, and movement insert |
| `release_stock` | Cancellation and reservation timeout overlap | Same pessimistic locking discipline |
| `consume_stock` | Payment capture races with cancellation | Same pessimistic locking discipline and state guard |
| `restock` | Concurrent deliveries or admin imports | Row lock plus atomic `on_hand` increment |
| `bulk_reserve` | Two checkouts lock shared products in different orders | Pessimistic locks acquired in ascending `product_id` order |

Do not replace these locks with optimistic CAS. Hot inventory rows have
high contention, so retries would waste work and reduce predictability.

## 2. Orders (`apps/orders/services.py`) - implemented

| Function | Race | Implemented mechanism |
|---|---|---|
| `place_order` | Two tabs submit checkout | Lock cart; reserve inventory and insert order rows in the same transaction |
| `cancel_order` | User cancellation races with payment capture | Lock order, validate status, then release inventory in stable order |

## 3. Payments (`apps/payments/services.py`) - implemented

| Function | Race | Implemented mechanism |
|---|---|---|
| `capture_payment` | Duplicate webhook or parallel pay clicks | Lock `PaymentIntent`, use status guard and idempotent external ID |
| `refund_payment` | Concurrent refund and chargeback | Same pessimistic locking discipline and status guard |
| `process_webhook` | Same signature reaches multiple instances | Unique `WebhookEvent.signature`; duplicate insert returns false |

## 4. Cart (`apps/cart/services.py`) - implemented

| Function | Race | Implemented mechanism |
|---|---|---|
| `add_item` | Repeated click or multiple tabs | Lock cart and update quantity under the lock |
| `update_item` | Cart edit races with checkout | Lock cart and reject mutation after checkout |

## 5. Users (`apps/users/services.py`) - implemented

| Function | Race | Implemented mechanism |
|---|---|---|
| `register_customer` | Duplicate username or email submission | Unique constraint plus atomic User/Customer creation |
| `adjust_loyalty_points` | Completion, refund, and admin adjustment overlap | Atomic SQL F-expression update; conditional deduction prevents negative points |

Loyalty points intentionally do **not** use optimistic CAS. They are a
pure counter, so one conditional SQL `UPDATE` can perform the arithmetic
and bump `version` without a read, lock, or retry loop.

## 6. Products (`apps/products/services.py`) - implemented

| Function | Race | Implemented mechanism |
|---|---|---|
| `update_product_price` | Two admins edit the same product version | Optimistic `version` CAS via `bump_version`; invalidate product cache after commit |

`PATCH /api/v1/products/products/{id}/price/` requires
`expected_version`. A stale version returns HTTP 409 with code
`stale_product_version`; human/admin conflicts are surfaced instead of
being automatically retried.

## 7. Cache layer (`core/cache/redis_cache.py`)

| Race | Mechanism |
|---|---|
| Thundering herd on a hot cache miss | NFR6-owned single-flight guard |
| Cache and database drift after a write | Writers call the NFR6 invalidation helper after commit |

## 8. Lock acquisition order

Transactions that lock several rows must acquire them in ascending
primary-key order. `bulk_reserve` applies this rule by sorting products
before locking them. Future multi-row locking must follow the same rule.

## 9. Test coverage

The NFR7 acceptance tests are:

- `tests/unit/test_nfr7_pessimistic_inventory.py`
- `tests/unit/test_nfr7_optimistic_product.py`
- `tests/unit/test_nfr7_atomic_loyalty.py`

These tests use threads and transactional database access to prove
correctness. Stress tests and benchmarks measure behavior under load but
do not replace these correctness tests.
