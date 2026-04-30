"""
Async invoice generation - [NFR3].

Why off the request path: rendering a PDF + uploading it to storage can
easily take 1-2 seconds. Doing that inside the checkout request would
multiply tail latency under load.
"""
from celery import shared_task


@shared_task(
    bind=True,
    name="tasks.invoicing.generate_invoice",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
)
def generate_invoice(self, order_id: int) -> None:
    """Render the invoice PDF, upload to storage, link it to the Order.

    [NFR3] Owner implements:
      1. fetch order + items.
      2. render PDF.
      3. persist URL on the Order (atomic update on Order.version).
    """
    # TODO [NFR3]
    raise NotImplementedError("NFR3 owner must implement generate_invoice")


@shared_task(name="tasks.invoicing.regenerate_failed_invoices")
def regenerate_failed_invoices() -> None:
    """Sweeper for orders whose invoice generation never completed."""
    # TODO [NFR3]
    raise NotImplementedError("NFR3 owner must implement regenerate_failed_invoices")
