"""
User-related models.

Concurrency notes:
 - Customer.wallet_balance is the simulated payment wallet. Payment capture
   must lock the customer row before checking/deducting it.
 - Customer.loyalty_points is updated from multiple flows (order completion,
   refunds, manual adjustments). It MUST be updated through
   apps.users.services.adjust_loyalty_points (never assigned directly) so
   the optimistic version field is honored. See [NFR1] / [NFR7].
"""
from django.conf import settings
from django.db import models


class Customer(models.Model):
    """Domain profile attached to the auth user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="customer",
    )
    phone = models.CharField(max_length=32, blank=True)
    wallet_balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    loyalty_points = models.PositiveIntegerField(default=0)

    # Optimistic-lock version. Bumped by every successful update via
    # core.concurrency.locks.bump_version. [NFR7]
    version = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Customer<{self.user_id}>"


class Address(models.Model):
    BILLING = "billing"
    SHIPPING = "shipping"
    KIND_CHOICES = [(BILLING, "Billing"), (SHIPPING, "Shipping")]

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="addresses"
    )
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    line1 = models.CharField(max_length=255)
    line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=128)
    region = models.CharField(max_length=128, blank=True)
    postal_code = models.CharField(max_length=32)
    country = models.CharField(max_length=2)
    is_default = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["customer", "kind"])]
