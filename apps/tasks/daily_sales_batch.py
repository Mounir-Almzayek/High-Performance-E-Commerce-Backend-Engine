"""
Daily sales batch job - [NFR4].

Runs every day at 02:00 UTC (see tasks/__init__.py beat schedule). It
walks yesterday's OrderItem rows in CHUNKS, computes aggregates per chunk
in parallel, then merges them into a single DailySalesReport row.

Flow:
  1. Determine the [start, end] window (yesterday in UTC).
  2. queryset = OrderItem.objects.filter(order__placed_at__range=(start, end),
                                          order__status__in=[PAID, SHIPPED, DELIVERED])
  3. core.batch.chunked.process_in_parallel(qs, _aggregate_chunk,
                                             chunk_size=1000, max_workers=8)
  4. Merge per-chunk DailySalesAggregator instances.
  5. Persist a single DailySalesReport row.
  6. Send summary notification.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

from celery import shared_task
from django.db import transaction

from apps.orders.models import DailySalesReport, Order
from core.batch.chunked import (
    DailySalesAggregator,
    process_in_parallel,
)

if TYPE_CHECKING:
    from apps.orders.models import OrderItem

logger = logging.getLogger("tasks.daily_sales")

CHUNK_SIZE = 1_000

MAX_WORKERS = 8


def _get_yesterday_window() -> tuple[datetime, datetime]:
    today = date.today()
    yesterday = today - timedelta(days=1)

    start = datetime.combine(
        yesterday, datetime.min.time()).replace(tzinfo=timezone.utc)
    end = datetime.combine(today, datetime.min.time()
                           ).replace(tzinfo=timezone.utc)

    return start, end


def _aggregate_chunk(chunk: list[OrderItem]) -> dict:
    aggregator = DailySalesAggregator()
    aggregator.feed(chunk)
    return aggregator.to_report_data()


@shared_task(
    name="tasks.daily_sales_batch.run_daily_sales",
    acks_late=True,
)
def run_daily_sales() -> None:
    # Import here to avoid circular imports
    from apps.orders.models import OrderItem

    logger.info("daily_sales.starting")

    start, end = _get_yesterday_window()
    report_date = start.date()

    logger.info("daily_sales.window", extra={
                "start": start.isoformat(), "end": end.isoformat()})

    completed_statuses = [Order.PAID, Order.SHIPPED, Order.DELIVERED]

    queryset = (
        OrderItem.objects
        .filter(
            order__placed_at__gte=start,
            order__placed_at__lt=end,
            order__status__in=completed_statuses,
        )
        .select_related("order")
    )

    total_items = queryset.count()
    logger.info("daily_sales.items_to_process", extra={"count": total_items})

    if total_items == 0:
        logger.info("daily_sales.no_data", extra={
                    "date": report_date.isoformat()})
        DailySalesReport.objects.get_or_create(
            date=report_date,
            defaults={
                "total_orders": 0,
                "total_revenue": 0,
                "total_items_sold": 0,
                "by_product": {},
            },
        )
        return

    chunk_results = process_in_parallel(
        queryset=queryset,
        handler=_aggregate_chunk,
        chunk_size=CHUNK_SIZE,
        max_workers=MAX_WORKERS,
    )

    logger.info("daily_sales.chunks_completed",
                extra={"chunks": len(chunk_results)})

    final_aggregator = DailySalesAggregator()
    for result in chunk_results:
        chunk_agg = DailySalesAggregator()
        chunk_agg.total_orders = result["total_orders"]
        chunk_agg.total_revenue = result["total_revenue"]
        chunk_agg.total_items_sold = result["total_items_sold"]
        chunk_agg.by_product = result["by_product"]
        chunk_agg._order_ids = set(result["order_ids"])

        final_aggregator = final_aggregator.merge(chunk_agg)

    with transaction.atomic():
        report, created = DailySalesReport.objects.update_or_create(
            date=report_date,
            defaults={
                "total_orders": final_aggregator.total_orders,
                "total_revenue": final_aggregator.total_revenue,
                "total_items_sold": final_aggregator.total_items_sold,
                "by_product": final_aggregator.by_product,
            },
        )

    action = "created" if created else "updated"
    logger.info(
        "daily_sales.completed",
        extra={
            "date": report_date.isoformat(),
            "action": action,
            "total_orders": final_aggregator.total_orders,
            "total_revenue": final_aggregator.total_revenue,
            "total_items_sold": final_aggregator.total_items_sold,
        },
    )

    return {
        "date": report_date.isoformat(),
        "total_orders": final_aggregator.total_orders,
        "total_revenue": final_aggregator.total_revenue,
        "total_items_sold": final_aggregator.total_items_sold,
    }
