"""
Daily sales batch job - [NFR4].

Runs every day at 02:00 UTC (see tasks/__init__.py beat schedule). It
walks yesterday's OrderItem rows in CHUNKS, computes aggregates per chunk
in parallel, then merges them into a single DailySalesReport row.

Reference flow (the NFR4 owner finishes the chunked + parallel parts):

  1. Determine the [start, end] window (yesterday in UTC).
  2. queryset = OrderItem.objects.filter(order__placed_at__range=(start, end),
                                          order__status__in=[PAID, SHIPPED, DELIVERED])
  3. core.batch.chunked.process_in_parallel(qs, _aggregate_chunk,
                                             chunk_size=1000, max_workers=8)
  4. Merge per-chunk DailySalesAggregator instances.
  5. Persist a single DailySalesReport row.
  6. Optionally send a summary notification.
"""
from celery import shared_task


@shared_task(
    name="tasks.daily_sales_batch.run_daily_sales",
    acks_late=True,
)
def run_daily_sales() -> None:
    """Entry point for the daily aggregation job."""
    # TODO [NFR4]: implement chunked + parallel aggregation.
    raise NotImplementedError("NFR4 owner must implement run_daily_sales")


def _aggregate_chunk(chunk: list) -> dict:
    """Per-chunk aggregator. Returned dict is merged later."""
    # TODO [NFR4]
    raise NotImplementedError("NFR4 owner must implement _aggregate_chunk")
