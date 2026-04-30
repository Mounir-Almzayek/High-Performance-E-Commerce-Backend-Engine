"""
Payment models.

In real systems payment gateways send asynchronous webhooks that race with
the user's foreground request. The combination of (Order, PaymentIntent)
must therefore be updated under a lock; idempotency on `external_id`
prevents the same webhook from being processed twice. [NFR1] / [NFR8]
"""
import uuid

from django.db import models

from apps.orders.models import Order


class PaymentIntent(models.Model):
    INIT = "init"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    REFUNDED = "refunded"
    STATUS_CHOICES = [
        (INIT, "Init"),
        (AUTHORIZED, "Authorized"),
        (CAPTURED, "Captured"),
        (FAILED, "Failed"),
        (REFUNDED, "Refunded"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="payments")

    # External identifier from the gateway (Stripe, PayPal, ...). UNIQUE to
    # provide idempotency for retried webhooks.
    external_id = models.CharField(max_length=128, unique=True, null=True, blank=True)

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=INIT)

    # Optimistic-lock version. Webhook + foreground both write here. [NFR7]
    version = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["order", "-created_at"]),
            models.Index(fields=["status"]),
        ]


class WebhookEvent(models.Model):
    """Idempotent log of every gateway webhook.

    The unique signature column lets us drop duplicates and replay safely
    after an outage.
    """

    signature = models.CharField(max_length=128, unique=True)
    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
