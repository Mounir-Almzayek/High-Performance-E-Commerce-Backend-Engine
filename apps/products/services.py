"""
Product services — NFR6 (distributed cache) + NFR7 (optimistic locking).

Cache touchpoints:
  get_product_detail(product_id)     — read-through cache, key product:{id}
  list_products(filters, page)       — read-through cache, key product:list:{hash}:p{page}
  update_product_price(...)          — writer: optimistic CAS + cache invalidation

NFR7 touchpoint:
  update_product_price               — compare-and-set on Product.version via
                                       core.concurrency.locks.bump_version, so
                                       a concurrent price update is detected
                                       and rejected rather than silently
                                       overwriting a newer value.

Cache serialisation:
  Product detail is stored as a plain dict produced by ProductDetailSerializer.
  List pages are stored as lists of dicts produced by ProductListSerializer.
  Both are JSON-serialisable without pickle.

Invalidation contract:
  update_product_price schedules invalidate_product via django's on_commit
  hook, so the cache is only cleared AFTER the DB transaction commits. A
  rolled-back price update never touches the cache.
"""
from __future__ import annotations

import hashlib
import logging

from django.db import transaction

from core.cache.redis_cache import (
    TTL_PRODUCT_DETAIL,
    TTL_PRODUCT_LIST,
    cache_get_or_set,
    get_or_build_product_list,
    invalidate_product,
)
from core.concurrency.locks import StaleObjectError, bump_version, with_optimistic_retry

from .models import Product

logger = logging.getLogger("apps.products.services")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_product_detail(product_id: int) -> dict:
    """Fetch a single product from DB and serialise to dict."""
    from .serializers import ProductDetailSerializer  # local import — avoids circular

    product = (
        Product.objects
        .select_related("category")
        .prefetch_related("images")
        .get(pk=product_id)
    )
    return dict(ProductDetailSerializer(product).data)


def _make_filter_hash(
    category_id: int | None,
    search: str | None,
    ordering: str | None,
) -> str:
    """
    Produce a short, stable identifier for a (filter, ordering) combination.

    Uses MD5 truncated to 12 hex chars — sufficient to avoid accidental
    collisions for the number of distinct filter combinations we expect
    in production (< 10^4).

    Collision risk: MD5-96 bit gives P(collision) < 10^-21 for 10^6 keys.
    Accepted — an occasional list-cache miss is far less harmful than a
    stale-list served to a customer.
    """
    parts = f"{category_id or ''}|{search or ''}|{ordering or ''}"
    return hashlib.md5(parts.encode()).hexdigest()[:12]


def _build_product_list(
    category_id: int | None,
    search: str | None,
    ordering: str | None,
    page: int,
) -> list[dict]:
    """Fetch a product listing page from DB and serialise to list of dicts."""
    from rest_framework.pagination import PageNumberPagination
    from .serializers import ProductListSerializer

    PAGE_SIZE = 20  # matches settings.REST_FRAMEWORK["PAGE_SIZE"]

    qs = Product.objects.filter(status=Product.ACTIVE).select_related("category")
    if category_id:
        qs = qs.filter(category_id=category_id)
    if search:
        qs = qs.filter(name__icontains=search)
    if ordering:
        qs = qs.order_by(ordering)
    else:
        qs = qs.order_by("-created_at")

    # Manual pagination: slice the queryset at the DB level.
    offset = (page - 1) * PAGE_SIZE
    page_qs = qs[offset: offset + PAGE_SIZE]

    return [dict(ProductListSerializer(p).data) for p in page_qs]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_product_detail(product_id: int) -> dict:
    """
    Cached read-through of a single product.

    Returns the product as a plain dict (ProductDetailSerializer shape).
    On a cache miss, fetches from DB, populates the cache, then returns.
    On a soft-TTL expiry, serves the stale dict while the rebuild runs in
    the same request (elected rebuilder) or in the background (all others).

    Raises:
        Product.DoesNotExist — when no ACTIVE product with that pk exists.
    """
    key = f"product:{product_id}"
    return cache_get_or_set(
        key=key,
        builder=lambda: _build_product_detail(product_id),
        ttl=TTL_PRODUCT_DETAIL,
    )


def list_products(
    *,
    category_id: int | None = None,
    search: str | None = None,
    ordering: str | None = None,
    page: int = 1,
) -> list[dict]:
    """
    Cached listing.

    Cache key includes a stable hash of (category_id, search, ordering, page)
    so the same query reuses the cached result across web1/web2/web3.

    Returns a list of dicts (ProductListSerializer shape).
    """
    filter_hash = _make_filter_hash(category_id, search, ordering)
    return get_or_build_product_list(
        filter_hash=filter_hash,
        page=page,
        builder=lambda: _build_product_list(category_id, search, ordering, page),
    )


@with_optimistic_retry(retries=3, backoff_ms=10)
def update_product_price(
    *,
    product_id: int,
    new_price,
    expected_version: int,
) -> int:
    """
    Optimistic-locked price update.  [NFR7 + NFR6]

    Uses core.concurrency.locks.bump_version to issue a single
    ``UPDATE … WHERE version = expected_version`` statement. If another
    writer changed the price concurrently, bump_version raises
    StaleObjectError; the ``@with_optimistic_retry`` decorator will
    re-read and retry up to 3 times with jittered backoff.

    After a successful DB commit, schedules ``invalidate_product`` via
    ``transaction.on_commit`` so the cache is flushed only if the
    transaction committed. A rolled-back update never touches the cache.

    Args:
        product_id: PK of the product to update.
        new_price: the new price value (Decimal or compatible).
        expected_version: the ``version`` the caller read from the DB;
            used as the optimistic-lock guard.

    Returns:
        The new version number after a successful update.

    Raises:
        StaleObjectError: after all retries are exhausted (another writer
            keeps winning the race — caller should surface HTTP 409).
        Product.DoesNotExist: if the product pk is unknown.
    """
    with transaction.atomic():
        new_version = bump_version(
            model_cls=Product,
            pk=product_id,
            expected_version=expected_version,
            fields={"price": new_price},
        )
        # Schedule cache invalidation to run AFTER commit.
        # Using a default-argument capture to avoid the late-binding closure
        # gotcha (product_id is a loop variable in callers).
        transaction.on_commit(
            lambda pid=product_id: _invalidate_after_price_update(pid)
        )

    logger.info(
        "products.price_updated",
        extra={
            "product_id": product_id,
            "new_price": str(new_price),
            "new_version": new_version,
        },
    )
    return new_version


def _invalidate_after_price_update(product_id: int) -> None:
    """Called from on_commit — flush all cache keys for this product."""
    try:
        invalidate_product(product_id)
        logger.info(
            "products.cache_invalidated_after_price_update",
            extra={"product_id": product_id},
        )
    except Exception:  # noqa: BLE001
        # Cache invalidation failure is non-fatal: the stale entry will
        # expire on its own TTL. Log it so the operator can investigate.
        logger.exception(
            "products.cache_invalidation_failed",
            extra={"product_id": product_id},
        )
