"""
Async notifications - [NFR3].

These tasks live OFF the request path. The user gets their HTTP 201 the
moment the order is persisted; the email / push goes out from a Celery
worker afterwards.

Owner of NFR3 implements the actual delivery and the retry policy.
"""
from celery import shared_task


@shared_task(
    bind=True,
    name="tasks.notifications.send_order_confirmation",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
)
def send_order_confirmation(self, order_id: int) -> None:
    """Send the order confirmation email to the customer.

    [NFR3] Triggered from apps.orders.services.place_order via
    transaction.on_commit. Must be IDEMPOTENT (same order_id may be
    retried by Celery on transient failures).
    """
    # TODO [NFR3]: render template, send via email backend, log delivery.
    raise NotImplementedError("NFR3 owner must implement send_order_confirmation")


@shared_task(name="tasks.notifications.send_low_stock_alert")
def send_low_stock_alert(product_id: int) -> None:
    """Notify procurement when stock crosses the reorder threshold."""
    # TODO [NFR3]
    raise NotImplementedError("NFR3 owner must implement send_low_stock_alert")


@shared_task(name="tasks.notifications.warm_product_cache")
def warm_product_cache() -> None:
    """Periodic cache warmer for the top-N products. [NFR6]"""
    # TODO [NFR6]: call core.cache.redis_cache.prefetch_top_products()
    raise NotImplementedError("NFR6 owner must implement warm_product_cache")
