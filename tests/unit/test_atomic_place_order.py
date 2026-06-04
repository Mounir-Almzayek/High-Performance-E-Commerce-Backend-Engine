"""
Atomicity (NFR8) — failure injection for the canonical composite write.

`place_order` creates the Order header + lines, then reserves inventory,
then closes the cart, all inside ONE transaction. If ANY step fails the
WHOLE thing must leave ZERO partial state behind: no order, no order
lines, no stock movement, and the cart still OPEN.

We prove it by injecting a failure into the inventory-reservation step
(which runs AFTER the Order + OrderItems were already INSERTed) and
asserting nothing survived the rollback.
"""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model

from apps.cart import services as cart_services
from apps.cart.models import Cart
from apps.inventory.models import StockItem, StockMovement
from apps.orders import services as order_services
from apps.orders.models import Order, OrderItem
from apps.products.models import Category, Product
from apps.users.models import Address, Customer

User = get_user_model()
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def open_cart_with_one_line():
    user = User.objects.create_user(username="carol", password="x")
    customer = Customer.objects.create(user=user, wallet_balance="500.00")
    addr = Address.objects.create(
        customer=customer, kind=Address.SHIPPING, line1="x",
        city="x", postal_code="00000", country="SY", is_default=True,
    )
    cat = Category.objects.create(name="C", slug="c")
    product = Product.objects.create(
        sku="P-1", name="P", slug="p", category=cat, price="100.00",
    )
    StockItem.objects.create(product=product, on_hand=10, reserved=0)

    cart_services.get_or_create_cart(customer)
    cart_services.add_item(customer=customer, product_id=product.id, quantity=2)
    return customer, addr, product


def test_place_order_rolls_back_completely_when_reservation_fails(
    open_cart_with_one_line, monkeypatch
):
    customer, addr, product = open_cart_with_one_line

    def boom(**kwargs):
        raise RuntimeError("injected reservation failure")

    # Patch the reservation step that runs AFTER Order/OrderItems are created.
    monkeypatch.setattr("apps.inventory.services.bulk_reserve", boom)

    with pytest.raises(RuntimeError, match="injected reservation failure"):
        order_services.place_order(
            customer=customer,
            shipping_address_id=addr.id,
            billing_address_id=addr.id,
        )

    # ZERO partial state survived the rollback.
    assert Order.objects.count() == 0
    assert OrderItem.objects.count() == 0
    assert StockMovement.objects.count() == 0

    cart = Cart.objects.get(customer=customer)
    assert cart.status == Cart.OPEN  # never advanced to CHECKED_OUT

    stock = StockItem.objects.get(product=product)
    assert stock.on_hand == 10
    assert stock.reserved == 0
