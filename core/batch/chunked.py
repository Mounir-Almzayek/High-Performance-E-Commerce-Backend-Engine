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
"""
from typing import Callable, Iterator


DEFAULT_CHUNK_SIZE = 1_000


def iter_in_chunks(queryset, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[list]:
    """Yield successive chunks of rows from a queryset without loading it all."""
    # TODO [NFR4]: use queryset.iterator(chunk_size=chunk_size) and group
    #              into lists locally.
    raise NotImplementedError("NFR4 owner must implement iter_in_chunks")


def process_in_parallel(
    queryset,
    handler: Callable[[list], None],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_workers: int | None = None,
) -> None:
    """Apply `handler` to chunks in parallel under the bounded executor."""
    # TODO [NFR4]: use core.resources.bounded_executor and submit one
    #              future per chunk; surface exceptions, do not swallow.
    raise NotImplementedError("NFR4 owner must implement process_in_parallel")


class DailySalesAggregator:
    """Running totals for the daily sales batch.

    NFR4 owner: implement merge() so per-chunk results can be combined
    safely after parallel processing (associative + commutative).
    """

    def __init__(self) -> None:
        self.total_orders = 0
        self.total_revenue = 0
        self.by_product: dict[int, int] = {}

    def feed(self, chunk: list) -> None:
        """Update aggregates from one chunk of OrderItem rows."""
        # TODO [NFR4]
        raise NotImplementedError

    def merge(self, other: "DailySalesAggregator") -> "DailySalesAggregator":
        """Combine two aggregators (used after parallel chunk processing)."""
        # TODO [NFR4]
        raise NotImplementedError
