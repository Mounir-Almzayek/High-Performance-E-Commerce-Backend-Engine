# Developer 6 — Distributed caching (NFR6)

## Your scope

You own cutting database pressure on the hottest read paths by serving
them from Redis. Because Redis is shared across web1/web2/web3, your cache
is **truly distributed** — killing one instance must not lose it.

Two things make this NFR pass or fail, and both are about correctness
more than speed: **invalidation** (a cached price must update when the
real price changes — no stale data) and **single-flight** (when a hot key
expires, only ONE request rebuilds it — no thundering herd). The examiner
will ask "what happens when the price changes?" — your answer is the
whole point.

## Files you will write code in

| File | What you'll do |
|---|---|
| `core/cache/redis_cache.py` | Implement `cache_get_or_set`, `invalidate_product`, `prefetch_top_products`; the TTL constants are already drafted. Add a single-flight guard. |
| `apps/products/services.py` | Wrap `get_product_detail` and `list_products` in read-through cache; call `invalidate_product` on every product mutation |
| `apps/inventory/services.py` | Short-TTL `inventory:level:{id}` cache (5 s) via a `get_level` helper; bust it on every `StockMovement` write |
| `apps/cart/services.py` | Cache `cart:{user_id}`; invalidate on every cart mutation |
| New task (e.g. `apps/products/tasks.py` or `tasks/`) | `warm_product_cache` on a 15-min beat schedule; elect ONE instance via a Redis lock |
| New file: `docs/benchmarks/nfr6-cache-impact.md` | The before/after numbers (feeds Dev 10) |

## Files you will read but not modify

- `docs/requirements/06-distributed-caching.md` — your spec (key patterns,
  TTLs, acceptance criteria).
- `config/settings/base.py` — confirm `CACHES["default"]` is
  `django_redis.cache.RedisCache`; you need `delete_pattern`.
- `core/concurrency/locks.py` — reuse `distributed_lock` for the
  single-flight rebuild AND the warmer election (Dev 1 / Dev 7 own it).
- `core/transactions/atomic.py` — invalidation must run via `on_commit`
  (Dev 8 owns it). See Tips.

## Definition of done

- All three `NotImplementedError` raises in `redis_cache.py` are gone.
- Every cached read path is served from Redis on the second call (prove
  with django-silk: query count drops to ~0 on a hit).
- Every writer invalidates via the helper. `cache.delete(...)` calls
  outside `core/cache/redis_cache.py` are prohibited — grep to confirm.
- Single-flight proven: under concurrent expiry, the DB shows **at most
  one rebuild per hot key**.
- After a price update, the next read returns the **new** price.
- `docs/benchmarks/nfr6-cache-impact.md` shows ≥ 10× drop in DB queries on
  the catalog browse path when warm.

## Tips

- **Invalidate on `on_commit`, never inline.** If you invalidate inside
  the transaction and it then rolls back, the cache is now empty and the
  next read repopulates it from the *old* committed row — or worse, you
  serve a value that never committed. Defer the bust until after commit.
- Use `delete_pattern("product:list:*")` for list keys; a price change can
  affect many listing pages, not just the detail key.
- Pick ONE thundering-herd strategy and defend the trade-off in the
  report: a short Redis lock (single-flight) OR soft-TTL (first reader
  rebuilds while others serve stale). Don't implement both.
- The warmer must elect a single instance via a Redis lock, otherwise
  web1/web2/web3 all warm the same keys three times.

## Demo prep

1. Cold call `GET /api/v1/products/{id}/`. Show the silk query count and
   latency (DB hit).
2. Call it again. Show ~0 DB queries and lower latency (Redis hit).
3. Update the product price, then read again — show the new price comes
   back (invalidation works).
4. Fire N concurrent readers at a just-expired hot key; show the DB
   rebuild count is exactly 1 (single-flight works).
