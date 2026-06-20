"""
Product services.

Cache touchpoints (owned by NFR6):
 - get_product_detail(product_id) -> dict      [NFR6] cached read-through
 - list_products(filters, page) -> list[dict]  [NFR6] cached per query hash
 - update_product_price(product_id, ...)       [NFR6] writer must invalidate
                                               [NFR7] optimistic update
"""
from django.db import transaction

from core.cache.redis_cache import invalidate_product
from core.concurrency.locks import bump_version

from .models import Product


def get_product_detail(product_id: int) -> Product:
    """Cached read-through of a single product.

    [NFR6] Implementation must use core.cache.redis_cache.cache_get_or_set
    with key f"product:{product_id}" and TTL_PRODUCT_DETAIL.
    """
    # TODO [NFR6]: wrap DB fetch with cache_get_or_set.
    return Product.objects.select_related("category").prefetch_related("images").get(pk=product_id)


def list_products(*, category_id: int | None = None, search: str | None = None, page: int = 1):
    """Cached listing.

    [NFR6] Cache key includes a stable hash of (filters, page) so the same
    query reuses the cached result. Owner must define the hashing scheme
    and document collisions.
    """
    qs = Product.objects.filter(status=Product.ACTIVE).select_related("category")
    if category_id:
        qs = qs.filter(category_id=category_id)
    if search:
        qs = qs.filter(name__icontains=search)
    return qs


def update_product_price(*, product_id: int, new_price, expected_version: int):
    """Optimistic-locked price update.

    [NFR7] Must compare-and-set on `version`, raise StaleObjectError on
    mismatch. After a successful update, must call
    core.cache.redis_cache.invalidate_product(product_id). [NFR6]
    """
    with transaction.atomic():
        bump_version(
            Product,
            pk=product_id,
            expected_version=expected_version,
            fields={"price": new_price},
        )
        product = Product.objects.get(pk=product_id)
        transaction.on_commit(lambda: _invalidate_product_safely(product_id))

    return product


def _invalidate_product_safely(product_id: int) -> None:
    """Allow product writes while the NFR6 cache invalidator is still a stub."""
    try:
        invalidate_product(product_id)
    except NotImplementedError:
        pass
