"""
Async notifications - [NFR3].

These tasks live OFF the request path. The user gets their HTTP 201 the
moment the order is persisted; the email / push goes out from a Celery
worker afterwards.

Owner of NFR3 implements the actual delivery and the retry policy.
"""

import time
import logging
from smtplib import SMTPException

from celery import shared_task
from django.utils import timezone

from apps.orders.models import OrderEmailDispatch

logger = logging.getLogger(__name__)


# =========================================================
# ORDER CONFIRMATION EMAIL (IDEMPOTENT + RETRY SAFE)
# =========================================================

@shared_task(
    bind=True,
    name="tasks.notifications.send_order_confirmation",
    autoretry_for=(SMTPException, ConnectionError),
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
)
def send_order_confirmation(self, order_id: int) -> None:
    """
    Async order confirmation task.

    Guarantees:
    - No duplicate emails (idempotency via OrderEmailDispatch)
    - Safe retries (Celery retry-safe state handling)
    - Failure tracking via status field
    """

    logger.info(f" Starting confirmation for order {order_id}")

    # Get or create dispatch record
    dispatch, _ = OrderEmailDispatch.objects.get_or_create(
        order_id=order_id
    )

    # ----------------------------
    # 1. ALREADY SENT → EXIT FAST
    # ----------------------------
    if dispatch.status == OrderEmailDispatch.SENT:
        logger.info(f" Email already SENT for order {order_id}, skipping.")
        return

    # ----------------------------
    # 2. MARK AS PENDING (safe retry visibility)
    # ----------------------------
    if dispatch.status != OrderEmailDispatch.PENDING:
        dispatch.status = OrderEmailDispatch.PENDING
        dispatch.save(update_fields=["status"])

    try:
        # simulate external email provider latency
        time.sleep(5)

        # ----------------------------
        # 3. SUCCESS STATE
        # ----------------------------
        dispatch.status = OrderEmailDispatch.SENT
        dispatch.sent_at = timezone.now()
        dispatch.save(update_fields=["status", "sent_at"])

        logger.info(f"Order confirmation SENT for order {order_id}")

        return None

    except Exception as e:
        # ----------------------------
        # 4. FAILURE STATE (safe retry path)
        # ----------------------------
        dispatch.status = OrderEmailDispatch.FAILED
        dispatch.save(update_fields=["status"])

        logger.error(f"Failed email for order {order_id}: {str(e)}")

        raise e


# =========================================================
# LOW STOCK ALERT (simple async task)
# =========================================================

@shared_task(name="tasks.notifications.send_low_stock_alert")
def send_low_stock_alert(product_id: int) -> None:
    """
    Async low stock alert.

    Triggered by inventory service when stock drops below threshold.
    """

    logger.info(f" Low stock alert for product {product_id}")

    time.sleep(3)

    logger.info(f" Procurement notified for product {product_id}")

    return None


# =========================================================
# CACHE WARMING periodic task)
# =========================================================

@shared_task(name="tasks.notifications.warm_product_cache")
def warm_product_cache() -> None:
    """
    Periodic cache warming task — NFR6.

    Runs every 15 minutes (beat schedule in tasks/__init__.py). Calls
    core.cache.redis_cache.prefetch_top_products which:
      1. Acquires a distributed Redis lock to ensure exactly ONE worker
         instance runs the warmer even when celery_worker is scaled.
      2. Fetches the top-100 products by order volume.
      3. Populates ``product:{id}`` keys with the read-through helper.

    If the lock is already held by another node, the task exits silently
    (the other node is doing the work).
    """
    from core.cache.redis_cache import prefetch_top_products

    logger.info("cache_warmer.starting")
    warmed = prefetch_top_products(n=100)
    logger.info("cache_warmer.done", extra={"warmed": warmed})