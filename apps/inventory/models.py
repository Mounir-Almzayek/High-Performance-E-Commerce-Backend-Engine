"""
Inventory models - the highest-contention module of the system.

  StockItem.on_hand and StockItem.reserved are written by:
   - place-order flow (reserves)
   - payment-capture flow (consumes the reservation)
   - cancellation flow (releases reservations)
   - daily batch reconciliation
   - admin / CSV imports

Without correct concurrency control these races yield oversold stock,
phantom reservations, or negative balances. Every writer to this table
MUST go through apps.inventory.services.
"""
from django.db import models

from apps.products.models import Product


class StockItem(models.Model):
    """One row per product (single warehouse for now)."""

    product = models.OneToOneField(
        Product, on_delete=models.CASCADE, related_name="stock_item"
    )
    on_hand = models.PositiveIntegerField(default=0)
    reserved = models.PositiveIntegerField(default=0)
    reorder_threshold = models.PositiveIntegerField(default=10)

    # Optimistic-lock version. [NFR7]
    version = models.PositiveIntegerField(default=0)

    updated_at = models.DateTimeField(auto_now=True)

    @property
    def available(self) -> int:
        return self.on_hand - self.reserved

    def __str__(self) -> str:
        return f"Stock<{self.product_id}: on_hand={self.on_hand}, reserved={self.reserved}>"


class StockMovement(models.Model):
    """Append-only log of every change to a StockItem.

    Audit trail used by NFR4 (daily batch) and NFR8 (root-cause analysis
    when a transaction's outcome is questioned).
    """

    INBOUND = "inbound"      # restock from supplier
    RESERVE = "reserve"      # cart reserves units
    RELEASE = "release"      # reservation cancelled
    CONSUME = "consume"      # paid order consumed reservation
    ADJUST = "adjust"        # manual correction
    KIND_CHOICES = [
        (INBOUND, "Inbound"),
        (RESERVE, "Reserve"),
        (RELEASE, "Release"),
        (CONSUME, "Consume"),
        (ADJUST, "Adjust"),
    ]

    stock_item = models.ForeignKey(
        StockItem, on_delete=models.PROTECT, related_name="movements"
    )
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    quantity = models.IntegerField()  # signed: positive = add, negative = remove
    reference = models.CharField(max_length=128, blank=True)  # e.g. order id
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["stock_item", "-created_at"]),
            models.Index(fields=["kind", "-created_at"]),
        ]
