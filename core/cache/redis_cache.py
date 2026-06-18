"""
Distributed cache layer - [NFR6].

Goals:
  - Cut DB pressure on the hottest read paths (catalog browse, product
    detail) by serving them from Redis.
  - Keep cache invalidation honest: every writer to a cached entity must
    invalidate via the helpers here, never directly via cache.delete().

Public surface (filled in by NFR6 owner):

  - cache_get_or_set(key, builder, ttl)
        Read-through cache. `builder` is a zero-arg callable that produces
        the value on a miss.

  - invalidate_product(product_id)
        Removes every key derived from this product (detail, listing
        pages, search results that include it).

  - prefetch_top_products(n=100)
        Warmer used by tasks/notifications.py on a schedule so the cache
        is hot before peak traffic.

Cache key conventions (centralized to avoid drift across callers):

  Pattern                                Owner
  -----------------------------------------------------------------
  product:{id}                           apps.products
  product:list:{filter_hash}:p{page}     apps.products
  cart:{user_id}                         apps.cart
  inventory:level:{product_id}           apps.inventory  (short TTL!)
  rate:{user_id}:{endpoint}              apps.users      (rate limiting)
"""
from typing import Any, Callable
import time
from django.core.cache import cache
from django_redis import get_redis_connection
from django.conf import settings

# TTLs grouped here so they can be tuned in one place.
TTL_PRODUCT_DETAIL = 60 * 10        # 10 minutes
TTL_PRODUCT_LIST = 60 * 2           # 2 minutes
TTL_INVENTORY_LEVEL = 5             # 5 seconds (must be small - hot data)
TTL_CART = 60 * 60                  # 1 hour


def cache_get_or_set(key: str, builder: Callable[[], Any], ttl: int) -> Any:
    """Read-through cache helper.

    NFR6 owner: implement using django.core.cache.cache, with a guard
    against the thundering-herd problem (consider a short-lived lock or
    `cache.add()` semantics).
    """
    # Simple read-through with a short-lived lock to avoid thundering-herd.
    # Behavior:
    # 1. Try cache.get()
    # 2. If miss, attempt to acquire a lightweight lock using cache.add(lock_key).
    #    - If acquired: run builder(), set cache, release lock
    #    - If not acquired: poll until value appears or timeout

    val = cache.get(key)
    if val is not None:
        return val

    lock_key = f"lock:{key}"
    # How long to wait for the builder to populate the cache (seconds)
    wait_timeout = float(getattr(settings, "CACHE_BUILDER_WAIT_SECONDS", 5))
    wait_until = time.monotonic() + wait_timeout

    # Try to become the builder
    acquired = cache.add(lock_key, "1", timeout=5)
    if acquired:
        try:
            # Double-check after acquiring lock
            val = cache.get(key)
            if val is not None:
                return val
            result = builder()
            # Allow None values to be cached explicitly; use cache.set
            cache.set(key, result, ttl)
            return result
        finally:
            try:
                cache.delete(lock_key)
            except Exception:
                # Never fail the request because a lock cleanup failed
                pass
    else:
        # Wait for the builder to populate the cache (polling)
        while time.monotonic() < wait_until:
            val = cache.get(key)
            if val is not None:
                return val
            time.sleep(0.05)
        # Last-resort: become a builder to avoid endless staleness
        acquired = cache.add(lock_key, "1", timeout=5)
        if acquired:
            try:
                result = builder()
                cache.set(key, result, ttl)
                return result
            finally:
                try:
                    cache.delete(lock_key)
                except Exception:
                    pass
        # As fallback, call builder without caching
        return builder()


def invalidate_product(product_id: int) -> None:
    """Remove every cache key that depends on this product."""
    # Use Django's cache API so key prefixes/versioning match cache.set/get.
    try:
        cache.delete(f"product:{product_id}")
        cache.delete(f"inventory:level:{product_id}")

        delete_pattern = getattr(cache, "delete_pattern", None)
        if delete_pattern is not None:
            delete_pattern("product:list:*")
        else:
            conn = get_redis_connection("default")
            keys = list(conn.scan_iter(match="*product:list:*", count=1000))
            if keys:
                conn.delete(*keys)
    except Exception:
        # Swallow to avoid bringing down writers; log is left to caller's logger
        pass


def prefetch_top_products(n: int = 100) -> None:
    """Warm the cache for the top-N products. Called by a scheduled task."""
    # Query the top-N products (by a heuristic) and populate their detail
    # cache entries. This function performs best-effort warmup and must be
    # idempotent and inexpensive relative to peak traffic.
    try:
        from apps.products.models import Product

        qs = Product.objects.order_by("-popularity")[:n]
        for p in qs:
            key = f"product:{p.id}"

            def _build(p=p):
                # A compact serializable representation expected by callers
                return {
                    "id": p.id,
                    "title": getattr(p, "title", None),
                    "price": getattr(p, "price", None),
                }

            # Use a short TTL for detail warm cache
            try:
                cache_get_or_set(key, _build, TTL_PRODUCT_DETAIL)
            except Exception:
                # Best-effort: ignore failures
                continue
    except Exception:
        # If Product model is not present or DB inaccessible, silently skip
        pass
