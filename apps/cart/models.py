from django.db import models

from apps.products.models import Product
from apps.users.models import Customer


class Cart(models.Model):
    OPEN = "open"
    CHECKED_OUT = "checked_out"
    ABANDONED = "abandoned"
    STATUS_CHOICES = [
        (OPEN, "Open"),
        (CHECKED_OUT, "Checked out"),
        (ABANDONED, "Abandoned"),
    ]

    customer = models.OneToOneField(
        Customer, on_delete=models.CASCADE, related_name="cart"
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=OPEN)

    # Optimistic-lock version - cart line edits race with checkout. [NFR7]
    version = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Cart<{self.customer_id}: {self.status}>"


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)

    # Snapshot of price at the moment the item was added (so price changes
    # do not silently mutate the user's cart).
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["cart", "product"], name="unique_product_per_cart"
            )
        ]
