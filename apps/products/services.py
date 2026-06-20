"""
Product services - NFR6 distributed cache + NFR7 optimistic locking.

Read paths return plain serializer-shaped dictionaries so Redis stores JSON-
serialisable values instead of lazy QuerySets or model instances. Write paths
use an optimistic compare-and-set on Product.version and invalidate cache keys
only after the database transaction commits.
"""
from __future__ import annotations

import hashlib
import logging

from django.db import transaction

from core.cache.redis_cache import (
    TTL_PRODUCT_DETAIL,
    cache_get_or_set,
    get_or_build_product_list,
    invalidate_product,
)
from core.concurrency.locks import bump_version, with_optimistic_retry

from .models import Product

logger = logging.getLogger("apps.products.services")


def _build_product_detail(product_id: int) -> dict:
    """Fetch a single product from DB and serialise it for the API."""
    from .serializers import ProductDetailSerializer

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
    """Create a stable short key fragment for a list filter combination."""
    parts = f"{category_id or ''}|{search or ''}|{ordering or ''}"
    return hashlib.md5(parts.encode()).hexdigest()[:12]


def _build_product_list(
    category_id: int | None,
    search: str | None,
    ordering: str | None,
    page: int,
) -> list[dict]:
    """Fetch one product listing page from DB and serialise it."""
    from .serializers import ProductListSerializer

    page_size = 20
    qs = Product.objects.filter(status=Product.ACTIVE).select_related("category")
    if category_id:
        qs = qs.filter(category_id=category_id)
    if search:
        qs = qs.filter(name__icontains=search)
    qs = qs.order_by(ordering or "-created_at")

    offset = (page - 1) * page_size
    return [dict(ProductListSerializer(product).data) for product in qs[offset: offset + page_size]]


def get_product_detail(product_id: int) -> dict:
    """Cached read-through for a single product detail response."""
    return cache_get_or_set(
        key=f"product:{product_id}",
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
    """Cached listing keyed by filter hash and page."""
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
    """Update product price with optimistic locking and post-commit cache busting."""
    if not Product.objects.filter(pk=product_id).exists():
        raise Product.DoesNotExist(f"Product with pk={product_id} does not exist.")

    with transaction.atomic():
        new_version = bump_version(
            model_cls=Product,
            pk=product_id,
            expected_version=expected_version,
            fields={"price": new_price},
        )
        transaction.on_commit(lambda pid=product_id: _invalidate_after_price_update(pid))

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
    """Flush product-dependent cache keys after the price update commits."""
    try:
        invalidate_product(product_id)
        logger.info(
            "products.cache_invalidated_after_price_update",
            extra={"product_id": product_id},
        )
    except Exception:
        logger.exception(
            "products.cache_invalidation_failed",
            extra={"product_id": product_id},
        )
