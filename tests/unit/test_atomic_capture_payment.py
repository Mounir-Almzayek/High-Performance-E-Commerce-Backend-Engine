"""
Atomicity (NFR8) — failure injection for payment capture.

`capture_payment` is the money-moving composite: it deducts the wallet,
flips the PaymentIntent to CAPTURED, transitions the Order to PAID, and
consumes inventory — all in ONE transaction. A failure anywhere must roll
back ALL of it, even after a real partial write has already happened.

We inject the failure AFTER `consume_stock` has genuinely written to
StockItem + StockMovement, then assert the rollback undid the stock write
*and* left the wallet, intent, and order untouched. This proves the whole
composite is all-or-nothing, not just the steps after the failure point.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.inventory import services as inventory_services
from apps.inventory.models import StockItem, StockMovement
from apps.orders.models import Order, OrderItem
from apps.payments import services as payment_services
from apps.payments.models import PaymentIntent
from apps.products.models import Category, Product
from apps.users.models import Address, Customer

User = get_user_model()
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def order_ready_to_capture():
    user = User.objects.create_user(username="dave", password="x")
    customer = Customer.objects.create(user=user, wallet_balance="200.00")
    addr = Address.objects.create(
        customer=customer, kind=Address.SHIPPING, line1="x",
        city="x", postal_code="00000", country="SY", is_default=True,
    )
    cat = Category.objects.create(name="C", slug="c")
    product = Product.objects.create(
        sku="P-1", name="P", slug="p", category=cat, price="100.00",
    )
    # on_hand=5, reserved=1 mirrors a product already reserved for this order.
    StockItem.objects.create(product=product, on_hand=5, reserved=1)

    order = Order.objects.create(
        customer=customer, shipping_address=addr, billing_address=addr,
        subtotal="100.00", tax="0", shipping_fee="0", total="100.00",
        currency="USD",
    )
    OrderItem.objects.create(
        order=order, product=product, product_sku=product.sku,
        product_name=product.name, unit_price="100.00", quantity=1,
        line_total="100.00",
    )
    intent = PaymentIntent.objects.create(
        order=order, amount="100.00", currency="USD",
    )
    return order, intent, product


def test_capture_rolls_back_every_write_after_a_late_failure(
    order_ready_to_capture, monkeypatch
):
    order, intent, product = order_ready_to_capture
    real_consume = inventory_services.consume_stock

    def consume_then_fail(**kwargs):
        real_consume(**kwargs)  # genuinely writes StockItem + StockMovement
        raise RuntimeError("injected failure after stock consume")

    monkeypatch.setattr(
        "apps.inventory.services.consume_stock", consume_then_fail
    )

    with pytest.raises(RuntimeError, match="after stock consume"):
        payment_services.capture_payment(intent_id=intent.id, external_id="ext-fail")

    intent.refresh_from_db()
    order.refresh_from_db()
    stock = StockItem.objects.get(product=product)
    customer = Customer.objects.get(pk=order.customer_id)

    # Despite a real write inside consume_stock, the whole composite rolled back.
    assert intent.status == PaymentIntent.INIT
    assert order.status == Order.PENDING
    assert stock.on_hand == 5
    assert stock.reserved == 1
    assert StockMovement.objects.count() == 0
    assert customer.wallet_balance == Decimal("200.00")
