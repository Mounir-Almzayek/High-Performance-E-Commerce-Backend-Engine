"""
Distributed cache layer - NFR6.

Redis stores shared cache entries for all Django instances. Hot catalogue reads
use read-through caching with a soft-TTL and a short Redis single-flight lock so
only one request rebuilds an expired key while the rest serve stale data or wait
briefly for the refreshed value.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Callable

from django.core.cache import cache
from django_redis import get_redis_connection

logger = logging.getLogger("core.cache")

TTL_PRODUCT_DETAIL = 60 * 10
TTL_PRODUCT_LIST = 60 * 2
TTL_INVENTORY_LEVEL = 5
TTL_CART = 60 * 60

SOFT_TTL_LEAD_SECONDS = 30
_REBUILD_LOCK_MS = 3_000
_LOCK_WAIT_POLL_MS = 50
_REBUILD_LOCK_PREFIX = "sflock:"
_WARMER_TOP_N = 100
_WARMER_LOCK_KEY = "lock:cache_warmer:product"
_WARMER_LOCK_MS = 60_000


def _wrap(value: Any, ttl: int) -> str:
    """Serialise a value with soft-expiry metadata."""
    return json.dumps({
        "value": value,
        "expires_at": time.time() + ttl - SOFT_TTL_LEAD_SECONDS,
    })


def _unwrap(raw: str | None) -> tuple[Any | None, bool]:
    if raw is None:
        return None, False
    try:
        obj = json.loads(raw)
        return obj["value"], time.time() >= obj["expires_at"]
    except (TypeError, json.JSONDecodeError, KeyError):
        logger.warning("cache.corrupted_entry", extra={"raw_prefix": str(raw)[:64]})
        return None, False


def _acquire_rebuild_lock(key: str) -> str | None:
    conn = get_redis_connection("default")
    token = uuid.uuid4().hex
    acquired = conn.set(f"{_REBUILD_LOCK_PREFIX}{key}", token, nx=True, px=_REBUILD_LOCK_MS)
    return token if acquired else None


def _release_rebuild_lock(key: str, token: str) -> None:
    lua_release = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""
    try:
        conn = get_redis_connection("default")
        conn.eval(lua_release, 1, f"{_REBUILD_LOCK_PREFIX}{key}", token)
    except Exception:
        logger.exception("cache.rebuild_lock_release_failed", extra={"key": key})


def cache_get_or_set(key: str, builder: Callable[[], Any], ttl: int) -> Any:
    """Read-through cache helper with soft-TTL single-flight rebuild."""
    raw = cache.get(key)
    value, soft_expired = _unwrap(raw)

    if value is not None and not soft_expired:
        logger.debug("cache.hit", extra={"key": key})
        return value

    rebuild_token: str | None = None

    if value is not None and soft_expired:
        rebuild_token = _acquire_rebuild_lock(key)
        if rebuild_token is None:
            logger.debug("cache.stale_serve", extra={"key": key})
            return value
        logger.debug("cache.soft_expired_rebuilding", extra={"key": key})
    else:
        rebuild_token = _acquire_rebuild_lock(key)
        if rebuild_token is None:
            deadline = time.monotonic() + (_REBUILD_LOCK_MS / 1000.0)
            while time.monotonic() < deadline:
                time.sleep(_LOCK_WAIT_POLL_MS / 1000.0)
                raw = cache.get(key)
                value, soft_expired = _unwrap(raw)
                if value is not None and not soft_expired:
                    logger.debug("cache.hit_after_wait", extra={"key": key})
                    return value
            rebuild_token = _acquire_rebuild_lock(key)
        logger.debug("cache.miss", extra={"key": key})

    try:
        fresh_value = builder()
        cache.set(key, _wrap(fresh_value, ttl), timeout=ttl)
        logger.debug("cache.populated", extra={"key": key, "ttl": ttl})
        return fresh_value
    finally:
        if rebuild_token is not None:
            _release_rebuild_lock(key, rebuild_token)


def invalidate_product(product_id: int) -> None:
    """Remove product detail, product list, and inventory-level cache entries."""
    detail_key = f"product:{product_id}"
    list_pattern = "product:list:*"
    inventory_key = f"inventory:level:{product_id}"

    try:
        cache.delete(detail_key)
        delete_pattern = getattr(cache, "delete_pattern", None)
        if delete_pattern is not None:
            delete_pattern(list_pattern)
        else:
            conn = get_redis_connection("default")
            keys = list(conn.scan_iter(match=f"*{list_pattern}", count=1000))
            if keys:
                conn.delete(*keys)
        cache.delete(inventory_key)
        logger.info(
            "cache.invalidated",
            extra={"product_id": product_id, "patterns": [detail_key, list_pattern, inventory_key]},
        )
    except Exception:
        logger.exception("cache.invalidation_failed", extra={"product_id": product_id})


def invalidate_cart(user_id: int) -> None:
    """Remove the cached cart representation for a customer."""
    try:
        cache.delete(f"cart:{user_id}")
        logger.debug("cache.cart_invalidated", extra={"user_id": user_id})
    except Exception:
        logger.exception("cache.cart_invalidation_failed", extra={"user_id": user_id})


def invalidate_inventory(product_id: int) -> None:
    """Remove the inventory-level cache entry for a product."""
    try:
        cache.delete(f"inventory:level:{product_id}")
        logger.debug("cache.inventory_invalidated", extra={"product_id": product_id})
    except Exception:
        logger.exception("cache.inventory_invalidation_failed", extra={"product_id": product_id})


def prefetch_top_products(n: int = _WARMER_TOP_N) -> int:
    """Warm popular product detail entries, with one worker elected by Redis."""
    from core.concurrency.locks import LockNotAcquired, distributed_lock

    try:
        with distributed_lock(_WARMER_LOCK_KEY, timeout_ms=_WARMER_LOCK_MS, blocking=False):
            return _do_prefetch_top_products(n)
    except LockNotAcquired:
        logger.info("cache.warmer_skipped_lock_held")
        return 0


def _do_prefetch_top_products(n: int) -> int:
    from apps.orders.models import Order, OrderItem
    from apps.products.models import Product

    top_ids = list(
        OrderItem.objects
        .filter(order__status__in=[Order.PAID, Order.SHIPPED, Order.DELIVERED])
        .values_list("product_id", flat=True)
        .order_by()
        .distinct()[:n]
    )

    if len(top_ids) < n:
        extra_ids = list(
            Product.objects
            .filter(status=Product.ACTIVE)
            .exclude(pk__in=top_ids)
            .order_by("-created_at")
            .values_list("pk", flat=True)[: n - len(top_ids)]
        )
        top_ids += extra_ids

    warmed = 0
    for product_id in top_ids:
        try:
            cache_get_or_set(
                key=f"product:{product_id}",
                builder=lambda pid=product_id: _build_product_detail(pid),
                ttl=TTL_PRODUCT_DETAIL,
            )
            warmed += 1
        except Exception:
            logger.exception("cache.warmer_product_failed", extra={"product_id": product_id})

    logger.info("cache.warmer_done", extra={"warmed": warmed})
    return warmed


def _build_product_detail(product_id: int) -> dict:
    from apps.products.models import Product
    from apps.products.serializers import ProductDetailSerializer

    product = (
        Product.objects
        .select_related("category")
        .prefetch_related("images")
        .get(pk=product_id)
    )
    return dict(ProductDetailSerializer(product).data)


def get_or_build_product_list(
    *,
    filter_hash: str,
    page: int,
    builder: Callable[[], list[dict]],
) -> list[dict]:
    """Read-through helper for cached product list pages."""
    key = f"product:list:{filter_hash}:p{page}"
    return cache_get_or_set(key=key, builder=builder, ttl=TTL_PRODUCT_LIST)
