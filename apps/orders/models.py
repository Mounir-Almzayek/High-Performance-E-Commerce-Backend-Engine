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

    public_id = models.UUIDField(
        default=uuid.uuid4, editable=False, unique=True)
    customer = models.ForeignKey(
        Customer, on_delete=models.PROTECT, related_name="orders"
    )

    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=PENDING)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    shipping_fee = models.DecimalField(
        max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=3, default="USD")
    invoice_url = models.URLField(null=True, blank=True)

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
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)

    # Snapshots taken at order placement.
    product_sku = models.CharField(max_length=64)
    product_name = models.CharField(max_length=255)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField()
    line_total = models.DecimalField(max_digits=12, decimal_places=2)


class DailySalesReport(models.Model):
    """Daily aggregation of sales - produced by NFR4 batch job."""

    date = models.DateField(unique=True, db_index=True)
    total_orders = models.PositiveIntegerField(default=0)
    total_revenue = models.DecimalField(
        max_digits=14, decimal_places=2, default=0)
    total_items_sold = models.PositiveIntegerField(default=0)

    # JSON field for per-product breakdown
    by_product = models.JSONField(default=dict)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date"]
        verbose_name = "Daily Sales Report"
        verbose_name_plural = "Daily Sales Reports"

    def __str__(self) -> str:
        return f"DailySalesReport<{self.date}: {self.total_orders} orders, ${self.total_revenue}>"


class OrderEmailDispatch(models.Model):
    """
    Prevent duplicate confirmation emails with proper state tracking.
    """

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (SENT, "Sent"),
        (FAILED, "Failed"),
    ]

    order = models.OneToOneField(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="email_dispatch",
    )

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=PENDING
    )

    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "order_email_dispatches"
