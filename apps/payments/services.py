"""
Payment services.

Why this is a concurrency hot-spot:
 - Two parallel webhooks for the same `external_id` may arrive on web1 and
   web2 simultaneously.
 - The user can also click "Pay" twice from two tabs.
 - Order status (paid / cancelled) and PaymentIntent.status must move in
   lockstep, never diverging.

Implementation requirements:
 - capture_payment must be idempotent on external_id.
 - The transition to PAID must consume reservations via
   apps.inventory.services.consume_stock inside the same transaction
   that flips Order.status. [NFR8]
 - Async invoice generation is dispatched via on_commit hook. [NFR3]
"""
from .models import PaymentIntent


def create_intent(*, order_id: int, amount, currency: str = "USD") -> PaymentIntent:
    """Create a fresh PaymentIntent in INIT status.

    Race-safe by design (one row per call, no external dependency yet).
    """
    return PaymentIntent.objects.create(
        order_id=order_id, amount=amount, currency=currency
    )


def capture_payment(*, intent_id: int, external_id: str) -> PaymentIntent:
    """Move PaymentIntent: AUTHORIZED -> CAPTURED, flip Order to PAID.

    [NFR1] Lock the PaymentIntent row, validate external_id is fresh,
           perform the state transition, and consume stock atomically.
    [NFR8] All-or-nothing.
    """
    # TODO [NFR1 + NFR8]: implement with select_for_update and consume_stock.
    raise NotImplementedError("Concurrency owner must implement capture_payment")


def refund_payment(*, intent_id: int, reason: str = "") -> PaymentIntent:
    """Move PaymentIntent to REFUNDED and restock the relevant items."""
    # TODO [NFR1 + NFR8]
    raise NotImplementedError("Concurrency owner must implement refund_payment")


def process_webhook(signature: str, payload: dict) -> None:
    """Idempotently process an inbound gateway webhook.

    [NFR1] Race between concurrent webhooks: the unique signature column on
    WebhookEvent is the deduplication primitive.
    """
    # TODO [NFR1]: insert WebhookEvent with signature, swallow IntegrityError
    #              on duplicates, then dispatch to capture/refund as needed.
    raise NotImplementedError("Concurrency owner must implement process_webhook")
