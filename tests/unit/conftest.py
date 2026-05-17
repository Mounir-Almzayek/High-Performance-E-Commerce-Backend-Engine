"""
Shared test fixtures.

`transactional_db` from pytest-django gives us a real transactional
database (vs. the wrapping-everything-in-a-rollback default), which is
required for any test that uses threads + select_for_update or
F-expressions, because each thread needs its own transaction.
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.inventory.models import StockItem, StockMovement
from apps.products.models import Category, Product
from apps.users.models import Customer

User = get_user_model()


@pytest.fixture
def category(db) -> Category:
    return Category.objects.create(name="Test", slug="test")


@pytest.fixture
def product(db, category) -> Product:
    return Product.objects.create(
        sku="TST-001",
        name="Test Product",
        slug="test-product",
        category=category,
        price="10.00",
    )


@pytest.fixture
def stock_item(db, product) -> StockItem:
    return StockItem.objects.create(product=product, on_hand=10, reserved=0)


@pytest.fixture
def two_products(db, category) -> tuple[Product, Product]:
    p1 = Product.objects.create(
        sku="TST-A", name="A", slug="a", category=category, price="5.00"
    )
    p2 = Product.objects.create(
        sku="TST-B", name="B", slug="b", category=category, price="5.00"
    )
    StockItem.objects.create(product=p1, on_hand=5, reserved=0)
    StockItem.objects.create(product=p2, on_hand=5, reserved=0)
    return p1, p2


@pytest.fixture
def customer(db) -> Customer:
    user = User.objects.create_user(username="alice", password="x")
    return Customer.objects.create(
        user=user,
        wallet_balance="1000.00",
        loyalty_points=100,
    )
