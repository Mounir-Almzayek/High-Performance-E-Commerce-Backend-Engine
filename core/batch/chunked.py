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
    chunk: list[T] = []

    for row in queryset.iterator(chunk_size=chunk_size):
        chunk.append(row)
        if len(chunk) >= chunk_size:
            yield chunk
            chunk = []

    if chunk:
        yield chunk


def process_in_parallel(
    queryset: models.QuerySet,
    handler: Callable[[list[T]], dict],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_workers: int | None = None,
) -> list[dict]:
    results: list[dict] = []

    with bounded_executor(
        max_workers=max_workers,
        resource="batch",
        thread_name_prefix="nfr4_batch_worker",
    ) as executor:
        futures = {
            executor.submit(handler, chunk): i
            for i, chunk in enumerate(iter_in_chunks(queryset, chunk_size))
        }

        logger.info("batch.submitted", extra={"chunks": len(futures)})

        for future in as_completed(futures):
            chunk_index = futures[future]
            try:
                result = future.result()
                results.append(result)
                logger.debug("batch.chunk_ok", extra={"chunk": chunk_index})
            except Exception as exc:
                # Surface the exception - do not swallow
                logger.error("batch.chunk_failed", extra={
                             "chunk": chunk_index, "error": str(exc)})
                raise

    logger.info("batch.completed", extra={"chunks_processed": len(results)})
    return results


class DailySalesAggregator:

    def __init__(self) -> None:
        self.total_orders = 0
        self.total_revenue = 0.0
        self.total_items_sold = 0
        self._order_ids: set[int] = set()
        self.by_product: dict[int, dict] = {}

    def feed(self, chunk: list) -> None:
        for item in chunk:
            self._order_ids.add(item.order_id)

            self.total_revenue += float(item.line_total)
            self.total_items_sold += item.quantity

            pid = item.product_id
            if pid not in self.by_product:
                self.by_product[pid] = {"quantity": 0,
                                        "revenue": 0.0, "sku": item.product_sku}
            self.by_product[pid]["quantity"] += item.quantity
            self.by_product[pid]["revenue"] += float(item.line_total)

        self.total_orders = len(self._order_ids)

    def merge(self, other: DailySalesAggregator) -> DailySalesAggregator:
        merged = DailySalesAggregator()

        merged._order_ids = self._order_ids | other._order_ids
        merged.total_orders = len(merged._order_ids)

        merged.total_revenue = self.total_revenue + other.total_revenue
        merged.total_items_sold = self.total_items_sold + other.total_items_sold

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
                if not merged.by_product[pid]["sku"]:
                    merged.by_product[pid]["sku"] = other.by_product[pid]["sku"]

        return merged

    def to_report_data(self) -> dict:
        return {
            "total_orders": self.total_orders,
            "total_revenue": round(self.total_revenue, 2),
            "total_items_sold": self.total_items_sold,
            "by_product": self.by_product,
            "order_ids": sorted(self._order_ids),
        }
