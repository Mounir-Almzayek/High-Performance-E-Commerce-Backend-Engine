"""
Product services.

Cache touchpoints (owned by NFR6):
 - get_product_detail(product_id) -> Product   [NFR6] cached read-through
 - list_products(filters, page) -> QuerySet    [NFR6] cached per query hash
 - update_product_price(product_id, ...)       [NFR6] writer invalidates
                                               [NFR7] optimistic update
"""
from __future__ import annotations

from django.db import transaction

from core.cache.redis_cache import (
    TTL_PRODUCT_DETAIL,
    TTL_PRODUCT_LIST,
    cache_get_or_set,
    invalidate_product,
    make_list_cache_key,
)

from .models import Product


# ---------------------------------------------------------------------------
# Read paths (cached)
# ---------------------------------------------------------------------------

def get_product_detail(product_id: int) -> Product:
    """Cached read-through for a single product.

    Cache key  : product:{product_id}
    TTL        : TTL_PRODUCT_DETAIL (10 minutes)
    Invalidated: by update_product_price and admin edit signal via
                 invalidate_product().

    The builder fetches the product with its category and images in a
    single query (select_related + prefetch_related) so the cached object
    is fully hydrated and callers never trigger lazy-load DB hits while
    reading from cache.
    """
    key = f"product:{product_id}"

    return cache_get_or_set(
        key,
        builder=lambda: (
            Product.objects
            .select_related("category")
            .prefetch_related("images")
            .get(pk=product_id)
        ),
        ttl=TTL_PRODUCT_DETAIL,
    )


def list_products(
    *,
    category_id: int | None = None,
    search: str | None = None,
    page: int = 1,
):
    """Cached product listing.

    Cache key  : product:list:{filter_hash}:p{page}
                 where filter_hash is a 12-char MD5 of the canonical
                 filter parameters (see make_list_cache_key).
    TTL        : TTL_PRODUCT_LIST (2 minutes)
    Invalidated: by invalidate_product() on any product mutation (broad
                 pattern delete on product:list:* — see redis_cache.py).

    The queryset is evaluated inside the builder so the cached value is
    a plain list, which is serialisable by django's cache framework.
    Storing a lazy QuerySet would force re-evaluation on every cache hit
    and defeat the purpose of caching.
    """
    key = make_list_cache_key(
        category_id=category_id,
        search=search,
        page=page,
    )

    def _build() -> list:
        qs = (
            Product.objects
            .filter(status=Product.ACTIVE)
            .select_related("category")
        )
        if category_id:
            qs = qs.filter(category_id=category_id)
        if search:
            qs = qs.filter(name__icontains=search)
        return list(qs)

    return cache_get_or_set(key, builder=_build, ttl=TTL_PRODUCT_LIST)


# ---------------------------------------------------------------------------
# Write paths (invalidate after commit)
# ---------------------------------------------------------------------------

class StaleObjectError(Exception):
    """Raised when the optimistic-lock version does not match."""


def update_product_price(
    *,
    product_id: int,
    new_price,
    expected_version: int,
) -> Product:
    """Optimistic-locked price update with post-commit cache invalidation.

    [NFR7] Compare-and-set on `version`: if the row's version does not
    match `expected_version` the update is skipped and StaleObjectError
    is raised, forcing the caller to re-read and retry.

    [NFR6] After a successful commit, invalidate_product() is scheduled
    via transaction.on_commit() so the cache is never cleared for a
    transaction that later rolls back.  The next read will repopulate the
    cache from the new DB state (acceptance criterion 3 in NFR6).

    Args:
        product_id      : PK of the product to update.
        new_price       : New decimal price value.
        expected_version: The version the caller last read; must match
                          the current DB version for the update to apply.

    Returns:
        The updated Product instance.

    Raises:
        Product.DoesNotExist: if the product is not found.
        StaleObjectError    : if another writer updated the row first.
    """
    with transaction.atomic():
        # UPDATE ... WHERE id=? AND version=? is a single atomic
        # compare-and-set — no SELECT needed to detect conflicts.
        updated_rows = Product.objects.filter(
            pk=product_id,
            version=expected_version,
        ).update(
            price=new_price,
            version=expected_version + 1,
        )

        if updated_rows == 0:
            # Either the product doesn't exist or the version mismatched.
            if not Product.objects.filter(pk=product_id).exists():
                raise Product.DoesNotExist(
                    f"Product with pk={product_id} does not exist."
                )
            raise StaleObjectError(
                f"product_id={product_id}: expected version {expected_version} "
                f"but the row has been updated by another writer. Re-read and retry."
            )

        # Schedule invalidation AFTER the transaction commits so the cache
        # is not cleared for a rolled-back update.
        transaction.on_commit(lambda: invalidate_product(product_id))

    # Return the refreshed instance.
    return Product.objects.select_related("category").prefetch_related("images").get(pk=product_id)
