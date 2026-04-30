# NFR6 — Distributed caching

> Owner: _unassigned_ — stub-ready in `core/cache/redis_cache.py` and
> `apps/products/services.py`.

## Objective

Reduce database pressure on the hottest read paths by caching them in
Redis. Because Redis is shared by web1 and web2, this is a **truly
distributed** cache, not a per-process one.

## Cached read paths

| Key pattern | Producer | TTL | Invalidator |
|---|---|---|---|
| `product:{id}` | `products.services.get_product_detail` | 10 min | `update_product_price`, admin edit signal |
| `product:list:{filter_hash}:p{page}` | `products.services.list_products` | 2 min | any product mutation in that filter |
| `inventory:level:{product_id}` | `inventory.services.get_level` (suggested helper) | 5 sec | every `StockMovement` write |
| `cart:{user_id}` | `cart.services.get_or_create_cart` | 1 hour | every cart mutation |
| `rate:{user_id}:{endpoint}` | rate limiter | 1 sec | TTL-only |

TTL constants live as module-level names in `core/cache/redis_cache.py`
so all callers share them.

## Read-through pattern

```python
def get_product_detail(product_id):
    return cache_get_or_set(
        f"product:{product_id}",
        builder=lambda: Product.objects.select_related("category")
                                       .prefetch_related("images")
                                       .get(pk=product_id),
        ttl=TTL_PRODUCT_DETAIL,
    )
```

## Thundering herd

When a hot key expires, every concurrent reader misses simultaneously and
hits the DB. Mitigations the owner must implement:

- A short-lived Redis lock around the rebuild ("single-flight").
- Soft-TTL: store value with logical `expires_at` 30 s before the hard
  TTL; the first reader to see "soft expired" rebuilds while others keep
  serving the stale value.

The choice (and the trade-off) must be documented in the report.

## Invalidation contract

> Every writer to a cached entity MUST invalidate via a helper here.
> Direct `cache.delete(key)` calls outside `core/cache/redis_cache.py`
> are prohibited.

Example:

```python
# apps/products/services.py
def update_product_price(*, product_id, new_price, expected_version):
    with atomic_with_isolation():
        # ... optimistic CAS ...
        on_commit(lambda: invalidate_product(product_id))
```

`invalidate_product` uses `django_redis.client.DefaultClient.delete_pattern`
to remove the detail key AND every list key that mentions the product.

## Cache warmer

`warm_product_cache` runs every 15 minutes (beat schedule) and pre-fetches
the top-N products so the cache is warm before the next traffic spike.
Election: which instance runs the warmer is decided by a Redis
distributed lock so we don't run it twice.

## Why Redis over Memcached

- Already in the stack (broker + sessions).
- Supports pattern-based deletion (`SCAN` + `DEL`) which we need for
  list-key invalidation.
- Lua scripts let us implement atomic compare-and-delete for the lock
  release primitive (NFR1).

## Acceptance criteria

1. NFR10 report shows ≥ 10× drop in DB queries on the catalog browse
   path when the cache is warm.
2. Killing one Django instance does not lose the cache (it lives in the
   shared Redis).
3. After a price update, the next read returns the new price (proves
   invalidation works).
4. Under concurrent expiry, the DB shows at most one rebuild per hot key
   (proves single-flight).

## Files to ship

- `core/cache/redis_cache.py` — `cache_get_or_set`, `invalidate_product`,
  `prefetch_top_products`.
- Calls in `apps/products/services.py`, `apps/cart/services.py`,
  `apps/inventory/services.py`.
- `docs/benchmarks/nfr6-cache-impact.md` with the before/after numbers.
