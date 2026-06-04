"""
Payment services - heavily concurrent surface.

Two distinct race classes solved here:

  1. Multiple writers on the same row (foreground "Pay" click + a
     gateway webhook arriving on the OTHER backend). Solved with
     `SELECT ... FOR UPDATE` on the PaymentIntent and the Order rows.

  2. Duplicate webhooks for the same event (gateways replay aggressively
     on transient network errors). Solved with a UNIQUE constraint on
     `WebhookEvent.signature`. The DB IntegrityError is the dedup
     primitive; we don't need an application-level "have I seen this?"
     check that would itself be racy.

The state-transition method also CONSUMES inventory in the same
transaction, so a successful capture moves money AND moves stock
together. A failure of either rolls back both.

Lecture references:
  - "Idempotency - design for at-least-once delivery" (Session 3)
    -> WebhookEvent.signature UNIQUE + on-IntegrityError-skip.
  - "Mutex / single-writer" (Session 1)
    -> select_for_update on PaymentIntent.
  - "ACID composite write" (NFR8)
    -> capture transitions intent + order + inventory in one atomic.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from rest_framework.exceptions import APIException

from apps.inventory import services as inventory_services
from apps.orders.models import Order, OrderItem
from apps.users.models import Customer
from core.aop.decorators import audit_log, timed
from core.resources.pool import capacity_limited
from core.transactions.atomic import atomic_with_isolation

from .models import PaymentIntent, WebhookEvent

logger = logging.getLogger("apps.payments")


# ----------------------------- exceptions ---------------------------------


class InvalidPaymentState(Exception):
    """The intent is not in a state that allows the requested transition."""


class DuplicateWebhook(Exception):
    """Webhook with this signature was processed before. Caller may ignore."""


class InsufficientWalletBalance(APIException):
    """The simulated wallet does not have enough funds for capture."""

    status_code = 402
    default_code = "insufficient_wallet_balance"

    def __init__(self, *, required, available) -> None:
        super().__init__(
            detail={
                "detail": "Insufficient wallet balance.",
                "required": str(required),
                "available": str(available),
            }
        )


# ----------------------------- public API ---------------------------------


@timed("payments.create_intent")
def create_intent(*, order_id: int, amount, currency: str = "USD") -> PaymentIntent:
    """Create a fresh PaymentIntent in INIT status.

    No locking required: this is an INSERT of a fresh row, not a
    read-modify-write on shared state.
    """
    return PaymentIntent.objects.create(
        order_id=order_id, amount=amount, currency=currency
    )


@timed("payments.capture_payment")
@audit_log("payments.capture_payment")
@capacity_limited("payment")
@atomic_with_isolation("read committed")
def capture_payment(*, intent_id: int, external_id: str) -> PaymentIntent:
    """Move a PaymentIntent from INIT/AUTHORIZED to CAPTURED.

    Inside the transaction:
      1. Lock the PaymentIntent row.
      2. If `external_id` is already set on the row and matches, this is
         a duplicate retry from the gateway - return idempotently.
      3. Lock the Order row, transition it to PAID.
      4. Lock the customer's simulated wallet and deduct the amount.
      5. Consume inventory for every OrderItem (one FOR UPDATE per stock
         row, taken in PK ASC order to stay deadlock-free).
      6. Persist the new state.

    Concurrency guarantees:
      - Two parallel calls with the same intent_id serialize on step 1.
      - The status guard in step 2 makes the call idempotent: a second
        successful caller is a no-op, NOT a double-charge.
    """
    intent = (
        PaymentIntent.objects
        .select_for_update()
        .select_related("order")
        .get(pk=intent_id)
    )

    # Idempotent: if already captured with the same external_id, return.
    if intent.status == PaymentIntent.CAPTURED:
        if intent.external_id == external_id:
            return intent
        raise InvalidPaymentState(
            "Intent already captured with a different external_id"
        )

    if intent.status not in (PaymentIntent.INIT, PaymentIntent.AUTHORIZED):
        raise InvalidPaymentState(
            f"Cannot capture intent in status={intent.status}"
        )

    # Lock the order, validate state.
    order = (
        Order.objects
        .select_for_update()
        .get(pk=intent.order_id)
    )
    if order.status not in (Order.PENDING,):
        raise InvalidPaymentState(
            f"Cannot capture payment for order in status={order.status}"
        )

    # Simulated wallet provider / payment latency. In automated tests this
    # defaults to 0; for the live demo set it to 2-5 seconds through env.
    delay_seconds = float(
        getattr(settings, "PAYMENT_CAPTURE_SIMULATED_DELAY_SECONDS", 0)
    )
    if delay_seconds > 0:
        time.sleep(delay_seconds)

    # Lock the customer wallet before checking and deducting. This is the
    # simulated payment step required for the project: no Stripe needed,
    # just a real balance check with race-free debit semantics.
    customer = Customer.objects.select_for_update().get(pk=order.customer_id)
    if customer.wallet_balance < intent.amount:
        raise InsufficientWalletBalance(
            required=intent.amount,
            available=customer.wallet_balance,
        )

    # Consume inventory. release_stock + consume_stock both lock rows
    # in PK ASC order via inventory.services helpers - safe.
    items = list(
        OrderItem.objects
        .filter(order=order)
        .order_by("product_id")  # ASC for predictable lock acquisition
    )
    for item in items:
        inventory_services.consume_stock(
            product_id=item.product_id,
            qty=item.quantity,
            reference=str(order.public_id),
        )

    # State transitions.
    Customer.objects.filter(pk=customer.pk).update(
        wallet_balance=F("wallet_balance") - intent.amount,
        version=F("version") + 1,
    )
    PaymentIntent.objects.filter(pk=intent.pk).update(
        status=PaymentIntent.CAPTURED,
        external_id=external_id,
        version=intent.version + 1,
    )
    Order.objects.filter(pk=order.pk).update(
        status=Order.PAID,
        version=order.version + 1,
    )

    intent.refresh_from_db()
    return intent


@timed("payments.refund_payment")
@audit_log("payments.refund_payment")
@capacity_limited("payment")
@atomic_with_isolation("read committed")
def refund_payment(*, intent_id: int, reason: str = "") -> PaymentIntent:
    """Refund a captured payment. Releases inventory back to stock."""
    intent = PaymentIntent.objects.select_for_update().get(pk=intent_id)
    if intent.status != PaymentIntent.CAPTURED:
        raise InvalidPaymentState(
            f"Cannot refund intent in status={intent.status}"
        )

    order = Order.objects.select_for_update().get(pk=intent.order_id)
    customer = Customer.objects.select_for_update().get(pk=order.customer_id)

    items = list(
        OrderItem.objects.filter(order=order).order_by("product_id")
    )
    for item in items:
        # Restock returns the units to on_hand. We do NOT touch
        # `reserved` here because at this point reserved is already 0
        # (consumed during capture).
        inventory_services.restock(
            product_id=item.product_id,
            qty=item.quantity,
            reference=f"refund:{order.public_id}",
        )

    Customer.objects.filter(pk=customer.pk).update(
        wallet_balance=F("wallet_balance") + intent.amount,
        version=F("version") + 1,
    )
    PaymentIntent.objects.filter(pk=intent.pk).update(
        status=PaymentIntent.REFUNDED,
        version=intent.version + 1,
    )
    Order.objects.filter(pk=order.pk).update(
        status=Order.CANCELLED,
        version=order.version + 1,
    )

    intent.refresh_from_db()
    return intent


@timed("payments.process_webhook")
@audit_log("payments.process_webhook")
@capacity_limited("payment")
def process_webhook(signature: str, payload: dict[str, Any]) -> bool:
    """Idempotently process an inbound gateway webhook.

    The UNIQUE index on WebhookEvent.signature is the dedup primitive:
    if the row already exists, the second INSERT raises IntegrityError
    and we return False without re-processing the side-effect.

    Returns True when the webhook was new and processed, False when it
    was a duplicate.
    """
    if not signature:
        raise InvalidPaymentState("missing webhook signature")

    # We attempt the INSERT in its own atomic block so the IntegrityError
    # does not poison the surrounding transaction (a failed atomic() in
    # Django marks the outer transaction as needing rollback).
    try:
        with transaction.atomic():
            WebhookEvent.objects.create(signature=signature, payload=payload)
    except IntegrityError:
        logger.info("webhook.duplicate", extra={"signature": signature})
        return False

    # Dispatch to the right handler. Each handler runs in its own
    # transaction (via the @transaction.atomic on capture/refund).
    event_type = payload.get("event")
    intent_id = payload.get("intent_id")
    if event_type == "payment_captured" and intent_id:
        capture_payment(
            intent_id=int(intent_id),
            external_id=payload.get("external_id", ""),
        )
    elif event_type == "payment_refunded" and intent_id:
        refund_payment(intent_id=int(intent_id), reason=payload.get("reason", ""))
    else:
        logger.info(
            "webhook.unrecognized",
            extra={"event": event_type, "signature": signature},
        )

    # Mark processed.
    WebhookEvent.objects.filter(signature=signature).update(processed_at=timezone.now())
    return True
