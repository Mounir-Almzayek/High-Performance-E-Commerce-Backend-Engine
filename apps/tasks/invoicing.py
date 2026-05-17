"""
Async invoice generation - [NFR3].

Why off the request path: rendering a PDF + uploading it to storage can
easily take 1-2 seconds. Doing that inside the checkout request would
multiply tail latency under load.
"""

import logging
import time

from celery import shared_task
from django.db import transaction

from apps.orders.models import Order

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    name="tasks.invoicing.generate_invoice",
    autoretry_for=(ConnectionError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
)
def generate_invoice(self, order_id: int) -> None:
    """
    Render invoice asynchronously and persist invoice URL.

    Idempotent:
    - If invoice_url already exists -> skip.
    """

    logger.info(f"Starting invoice generation for order {order_id}")

    try:
        order = Order.objects.get(id=order_id)

    except Order.DoesNotExist:
        logger.warning(f"Order {order_id} does not exist")
        return None

    # =========================
    # IDEMPOTENCY GUARD
    # =========================
    if order.invoice_url:
        logger.info(
            f"Invoice already exists for order {order_id}, skipping."
        )
        return None

    # simulate expensive PDF rendering
    time.sleep(4)

    # fake generated invoice URL
    invoice_url = (
        f"https://storage.example.com/invoices/order-{order_id}.pdf"
    )

    # atomic update
    with transaction.atomic():
        updated = Order.objects.filter(
            id=order_id,
            invoice_url__isnull=True,
        ).update(
            invoice_url=invoice_url,
            version=order.version + 1,
        )

    if updated == 0:
        logger.info(
            f"Invoice already generated concurrently for order {order_id}"
        )
        return None

    logger.info(
        f"Invoice generated successfully for order {order_id}"
    )

    return None


@shared_task(name="tasks.invoicing.regenerate_failed_invoices")
def regenerate_failed_invoices() -> None:
    """
    Retry invoice generation for orders missing invoice URLs.
    """

    logger.info("Starting failed invoice regeneration sweep")

    failed_orders = Order.objects.filter(invoice_url__isnull=True)

    for order in failed_orders:
        generate_invoice.delay(order.id)

    logger.info(
        f"Queued regeneration for {failed_orders.count()} orders"
    )

    return None