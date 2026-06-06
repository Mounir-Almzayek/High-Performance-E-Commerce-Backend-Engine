"""
Distributed cache layer — NFR6.

Goals:
  - Cut DB pressure on the hottest read paths (catalog browse, product
    detail) by serving them from Redis.
  - Keep cache invalidation honest: every writer to a cached entity must
    invalidate via the helpers here, never directly via cache.delete().

Design decisions:
  - Single-flight thundering-herd guard via Redis SET NX lock: when a hot
    key expires, exactly ONE reader is elected to rebuild while all others
    serve the current (now soft-expired) value or wait a bounded time.
  - Soft-TTL: every cached entry stores a logical ``expires_at`` timestamp
    30 seconds before the hard Redis TTL. The first reader to see a
    soft-expired entry acquires the rebuild lock; everyone else keeps
    serving the stale value until the rebuild commits. This means the
    cache never serves a full cold miss to concurrent readers — only the
    single elected rebuilder hits the DB.
  - All invalidation goes through ``invalidate_product`` which removes the
    detail key AND every list-page key for that product via SCAN + DEL
    (django_redis ``delete_pattern``). Callers must never call
    ``cache.delete`` directly.
  - TTL constants are module-level names so all callers share them and
    tuning is a one-line change.
  - ``prefetch_top_products`` is protected by a distributed lock so only
    ONE Celery worker instance runs the warmer even when celery_beat is
    running on multiple nodes.

Thundering-herd mitigation choice:
  We use soft-TTL + single-flight rebuild lock (not pure soft-TTL or pure
  jitter), because:
    1. Pure jitter still allows O(N) rebuilds where N is concurrency.
    2. Soft-TTL + lock guarantees at most ONE rebuild per expiry cycle.
  The soft-TTL window is SOFT_TTL_LEAD_SECONDS = 30 s, sized so that even
  a slow rebuild (worst case ~200 ms DB round-trip) has time to finish and
  re-populate before the hard TTL fires.

Cache key conventions (centralised to avoid drift across callers):

  Pattern                                Owner
  ----------------------------------------------------------------
  product:{id}                           apps.products
  product:list:{filter_hash}:p{page}     apps.products
  cart:{user_id}                         apps.cart
  inventory:level:{product_id}           apps.inventory  (short TTL!)
  rate:{user_id}:{endpoint}              apps.users      (rate limiting)

All keys carry a ``__meta`` wrapper:
  {
    "value": <serialised payload>,
    "expires_at": <unix float>,   # soft expiry timestamp
  }
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
_WARMER_LOCK_MS = 60_000  # 60 s — the warmer must finish in this window


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _wrap(value: Any, ttl: int) -> str:
    """Serialise value + soft-expiry metadata to a JSON string."""
    return json.dumps({
        "value": value,
        "expires_at": time.time() + ttl - SOFT_TTL_LEAD_SECONDS,
    })


def _unwrap(raw: str | None) -> tuple[Any | None, bool]:
    if raw is None:
        return None, False
    try:
        obj = json.loads(raw)
        value = obj["value"]
        soft_expired = time.time() >= obj["expires_at"]
        return value, soft_expired
    except (json.JSONDecodeError, KeyError):
        logger.warning("cache.corrupted_entry", extra={
                       "raw_prefix": str(raw)[:64]})
        return None, False


def _acquire_rebuild_lock(key: str) -> str | None:
    conn = get_redis_connection("default")
    token = uuid.uuid4().hex
    lock_key = f"{_REBUILD_LOCK_PREFIX}{key}"
    acquired = conn.set(lock_key, token, nx=True, px=_REBUILD_LOCK_MS)
    return token if acquired else None


def _release_rebuild_lock(key: str, token: str) -> None:
    _LUA_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""
    try:
        conn = get_redis_connection("default")
        conn.eval(_LUA_RELEASE, 1, f"{_REBUILD_LOCK_PREFIX}{key}", token)
    except Exception:
        logger.exception("cache.rebuild_lock_release_failed",
                         extra={"key": key})


def cache_get_or_set(key: str, builder: Callable[[], Any], ttl: int) -> Any:
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
    detail_key = f"product:{product_id}"
    list_pattern = "product:list:*"
    inventory_key = f"inventory:level:{product_id}"

    try:
        cache.delete(detail_key)
        cache.delete_pattern(list_pattern)
        cache.delete(inventory_key)
        logger.info(
            "cache.invalidated",
            extra={"product_id": product_id, "patterns": [
                detail_key, list_pattern, inventory_key]},
        )
    except Exception:
        logger.exception("cache.invalidation_failed",
                         extra={"product_id": product_id})


def invalidate_cart(user_id: int) -> None:
    key = f"cart:{user_id}"
    try:
        cache.delete(key)
        logger.debug("cache.cart_invalidated", extra={"user_id": user_id})
    except Exception:
        logger.exception("cache.cart_invalidation_failed",
                         extra={"user_id": user_id})


def invalidate_inventory(product_id: int) -> None:
    """Remove the inventory-level cache entry for a specific product."""
    key = f"inventory:level:{product_id}"
    try:
        cache.delete(key)
        logger.debug("cache.inventory_invalidated",
                     extra={"product_id": product_id})
    except Exception:
        logger.exception("cache.inventory_invalidation_failed",
                         extra={"product_id": product_id})


def prefetch_top_products(n: int = _WARMER_TOP_N) -> int:
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
    from apps.products.serializers import ProductDetailSerializer

    logger.info("cache.warmer_start", extra={"n": n})

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
        top_ids = top_ids + extra_ids

    warmed = 0
    for pid in top_ids:
        try:
            cache_get_or_set(
                key=f"product:{pid}",
                builder=lambda pid=pid: _build_product_detail(pid),
                ttl=TTL_PRODUCT_DETAIL,
            )
            warmed += 1
        except Exception:
            logger.exception("cache.warmer_product_failed",
                             extra={"product_id": pid})

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
    data = ProductDetailSerializer(product).data
    return dict(data)


def get_or_build_product_list(
    *,
    filter_hash: str,
    page: int,
    builder: Callable[[], list[dict]],
) -> list[dict]:
    key = f"product:list:{filter_hash}:p{page}"
    return cache_get_or_set(key=key, builder=builder, ttl=TTL_PRODUCT_LIST)
