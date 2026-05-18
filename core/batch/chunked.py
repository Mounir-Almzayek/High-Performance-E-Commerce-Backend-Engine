"""
Chunked batch processing helpers - [NFR4].

Why chunks: loading a full day of orders into memory will OOM on real data.
Streaming the queryset in fixed-size windows keeps memory bounded and lets
each chunk be processed (and committed) independently.

Public surface (filled in by NFR4 owner):

  - iter_in_chunks(queryset, chunk_size=1000)
        Yields lists of rows of size <= chunk_size. Must use the database
        cursor / `iterator(chunk_size=...)` to avoid materializing the
        whole queryset.

  - process_in_parallel(queryset, handler, chunk_size, max_workers)
        Fans chunks out across a bounded thread pool. Reuses
        core.resources.bounded_executor so [NFR2] caps still apply.

  - DailySalesAggregator
        Specific aggregator used by tasks.daily_sales_batch. Holds running
        totals and emits a single DailySalesReport row at the end.
        merge() is associative + commutative for safe parallel combining.
"""
from __future__ import annotations

import logging
from concurrent.futures import as_completed
from typing import Callable, Iterator, TypeVar

from django.db import models

from core.resources.pool import bounded_executor

logger = logging.getLogger("core.batch")

T = TypeVar("T")

DEFAULT_CHUNK_SIZE = 1_000


def iter_in_chunks(queryset: models.QuerySet, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[list[T]]:
    """Yield successive chunks of rows from a queryset without loading it all.

    Uses queryset.iterator() to stream rows from the database cursor
    instead of loading all rows into memory at once.

    Args:
        queryset: Django QuerySet to iterate over
        chunk_size: Number of rows per chunk

    Yields:
        Lists of model instances, each list has at most chunk_size items

    Example:
        >>> qs = OrderItem.objects.filter(order__placed_at__date=yesterday)
        >>> for chunk in iter_in_chunks(qs, chunk_size=1000):
        ...     process_chunk(chunk)  # chunk is a list of 1000 OrderItems
    """
    chunk: list[T] = []

    # iterator() streams rows from the DB cursor without caching
    # chunk_size hint tells the DB driver how many rows to fetch per round-trip
    for row in queryset.iterator(chunk_size=chunk_size):
        chunk.append(row)
        if len(chunk) >= chunk_size:
            yield chunk
            chunk = []

    # Yield any remaining rows in the final partial chunk
    if chunk:
        yield chunk


def process_in_parallel(
    queryset: models.QuerySet,
    handler: Callable[[list[T]], dict],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_workers: int | None = None,
) -> list[dict]:
    """DEMO BEFORE VERSION: process all rows at once without chunked fan-out.

    This intentionally materializes the queryset so the before-demo can
    show why chunking is needed for large daily sales jobs.

    Args:
        queryset: Django QuerySet to process
        handler: Function that takes a chunk (list of rows) and returns a dict
        chunk_size: Number of rows per chunk
        max_workers: Max threads to use (defaults to settings.INTERNAL_POOL_MAX_CONCURRENCY)

    Returns:
        List of results from each chunk handler

    Raises:
        Exception: Re-raises the first exception encountered (does not swallow)

    Example:
        >>> results = process_in_parallel(qs, _aggregate_chunk, chunk_size=1000, max_workers=8)
        >>> # results is a list of dicts, one per chunk
    """
    rows = list(queryset)
    logger.info("batch.before_demo_materialized", extra={"rows": len(rows)})
    return [handler(rows)] if rows else []


class DailySalesAggregator:
    """Running totals for the daily sales batch.

    NFR4 owner: implement merge() so per-chunk results can be combined
    safely after parallel processing (associative + commutative).
    """

    def __init__(self) -> None:
        self.total_orders = 0
        self.total_revenue = 0.0
        self.total_items_sold = 0
        # order_ids tracks unique orders for counting (not just items)
        self._order_ids: set[int] = set()
        # by_product: {product_id: {"quantity": int, "revenue": float}}
        self.by_product: dict[int, dict] = {}

    def feed(self, chunk: list) -> None:
        """Update aggregates from one chunk of OrderItem rows.

        Args:
            chunk: List of OrderItem instances from one chunk

        Updates:
            - total_orders: count of unique orders
            - total_revenue: sum of line_total
            - total_items_sold: sum of quantities
            - by_product: per-product breakdown
        """
        for item in chunk:
            # Track unique orders
            self._order_ids.add(item.order_id)

            # Sum revenue and items
            self.total_revenue += float(item.line_total)
            self.total_items_sold += item.quantity

            # Per-product breakdown
            pid = item.product_id
            if pid not in self.by_product:
                self.by_product[pid] = {"quantity": 0,
                                        "revenue": 0.0, "sku": item.product_sku}
            self.by_product[pid]["quantity"] += item.quantity
            self.by_product[pid]["revenue"] += float(item.line_total)

        # Update the public counter from the set
        self.total_orders = len(self._order_ids)

    def merge(self, other: DailySalesAggregator) -> DailySalesAggregator:
        """Combine two aggregators (used after parallel chunk processing).

        This operation is ASSOCIATIVE and COMMUTATIVE:
        - Can merge chunks in any order
        - Can merge partial results hierarchically

        Args:
            other: Another DailySalesAggregator instance

        Returns:
            New DailySalesAggregator with combined totals
        """
        merged = DailySalesAggregator()

        # Combine order IDs (set union for uniqueness)
        merged._order_ids = self._order_ids | other._order_ids
        merged.total_orders = len(merged._order_ids)

        # Sum numeric fields
        merged.total_revenue = self.total_revenue + other.total_revenue
        merged.total_items_sold = self.total_items_sold + other.total_items_sold

        # Merge per-product data
        all_products = set(self.by_product.keys()) | set(
            other.by_product.keys())
        for pid in all_products:
            merged.by_product[pid] = {"quantity": 0, "revenue": 0.0, "sku": ""}

            if pid in self.by_product:
                merged.by_product[pid]["quantity"] += self.by_product[pid]["quantity"]
                merged.by_product[pid]["revenue"] += self.by_product[pid]["revenue"]
                merged.by_product[pid]["sku"] = self.by_product[pid]["sku"]

            if pid in other.by_product:
                merged.by_product[pid]["quantity"] += other.by_product[pid]["quantity"]
                merged.by_product[pid]["revenue"] += other.by_product[pid]["revenue"]
                # Prefer other's sku if we don't have one
                if not merged.by_product[pid]["sku"]:
                    merged.by_product[pid]["sku"] = other.by_product[pid]["sku"]

        return merged

    def to_report_data(self) -> dict:
        """Convert to dict for DailySalesReport creation.

        Returns:
            Dict with keys: total_orders, total_revenue, total_items_sold, by_product
        """
        return {
            "total_orders": self.total_orders,
            "total_revenue": round(self.total_revenue, 2),
            "total_items_sold": self.total_items_sold,
            "by_product": self.by_product,
            "order_ids": sorted(self._order_ids),
        }
