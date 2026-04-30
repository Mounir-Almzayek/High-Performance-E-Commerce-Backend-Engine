"""
Catalog models.

Hot reads here: product detail and listing pages dominate traffic in
e-commerce. They are the primary target for the [NFR6] cache layer.
"""
from django.db import models


class Category(models.Model):
    name = models.CharField(max_length=128, unique=True)
    slug = models.SlugField(max_length=128, unique=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="children",
    )

    class Meta:
        verbose_name_plural = "categories"
        indexes = [models.Index(fields=["parent"])]

    def __str__(self) -> str:
        return self.name


class Product(models.Model):
    ACTIVE = "active"
    ARCHIVED = "archived"
    STATUS_CHOICES = [(ACTIVE, "Active"), (ARCHIVED, "Archived")]

    sku = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    description = models.TextField(blank=True)

    category = models.ForeignKey(
        Category, on_delete=models.PROTECT, related_name="products"
    )
    price = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="USD")

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=ACTIVE)

    # Optimistic-lock version for price/metadata updates. [NFR7]
    version = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "category"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.sku} - {self.name}"


class ProductImage(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="images"
    )
    url = models.URLField(max_length=1024)
    alt = models.CharField(max_length=255, blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position"]
