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
    # TODO [NFR6]
    raise NotImplementedError("NFR6 owner must implement cache_get_or_set")


def invalidate_product(product_id: int) -> None:
    """Remove every cache key that depends on this product."""
    # TODO [NFR6]: delete by pattern (django_redis exposes `delete_pattern`).
    raise NotImplementedError("NFR6 owner must implement invalidate_product")


def prefetch_top_products(n: int = 100) -> None:
    """Warm the cache for the top-N products. Called by a scheduled task."""
    # TODO [NFR6]
    raise NotImplementedError("NFR6 owner must implement prefetch_top_products")
