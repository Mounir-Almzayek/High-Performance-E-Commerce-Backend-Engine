"""
Order models.

`place_order` is the canonical composite-write of the system: it must
reserve stock, snapshot prices, create the Order + OrderItems, and queue
async side effects - all atomically. See [NFR8].
"""
import uuid

from django.db import models

from apps.products.models import Product
from apps.users.models import Address, Customer


class Order(models.Model):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (PENDING, "Pending payment"),
        (PAID, "Paid"),
        (SHIPPED, "Shipped"),
        (DELIVERED, "Delivered"),
        (CANCELLED, "Cancelled"),
    ]

    public_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="orders"
    )

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=PENDING)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    shipping_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="USD")

    shipping_address = models.ForeignKey(
        Address, on_delete=models.PROTECT, related_name="+", null=True
    )
    billing_address = models.ForeignKey(
        Address, on_delete=models.PROTECT, related_name="+", null=True
    )

    # Optimistic-lock version. Status transitions race with admin tools and
    # async payment webhooks. [NFR7]
    version = models.PositiveIntegerField(default=0)

    placed_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["customer", "-placed_at"]),
            models.Index(fields=["status", "-placed_at"]),
        ]

    def __str__(self) -> str:
        return f"Order<{self.public_id}: {self.status} {self.total} {self.currency}>"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)

    # Snapshots taken at order placement.
    product_sku = models.CharField(max_length=64)
    product_name = models.CharField(max_length=255)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField()
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
