"""
Concurrency tests for payment capture and webhook idempotency.

Lecture mapping:
  - "Idempotency in messaging systems" (Session 3) -> two webhooks with
    the same signature must produce exactly one side-effect.
  - "Mutex / single writer" (Session 1) -> two parallel capture calls
    on the same intent must serialize and produce exactly one
    transition.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections

from apps.inventory.models import StockItem
from apps.orders.models import Order, OrderItem
from apps.payments import services
from apps.payments.models import PaymentIntent, WebhookEvent
from apps.products.models import Category, Product
from apps.users.models import Address, Customer

User = get_user_model()
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def order_with_one_item(db):
    user = User.objects.create_user(username="bob", password="x")
    customer = Customer.objects.create(user=user, wallet_balance="200.00")
    addr = Address.objects.create(
        customer=customer, kind=Address.SHIPPING, line1="x",
        city="x", postal_code="00000", country="SY", is_default=True,
    )
    cat = Category.objects.create(name="C", slug="c")
    p = Product.objects.create(
        sku="P-1", name="P", slug="p", category=cat, price="100.00",
    )
    StockItem.objects.create(product=p, on_hand=5, reserved=1)

    order = Order.objects.create(
        customer=customer,
        shipping_address=addr,
        billing_address=addr,
        subtotal="100.00", tax="0", shipping_fee="0", total="100.00",
        currency="USD",
    )
    OrderItem.objects.create(
        order=order, product=p,
        product_sku=p.sku, product_name=p.name,
        unit_price="100.00", quantity=1, line_total="100.00",
    )
    intent = PaymentIntent.objects.create(
        order=order, amount="100.00", currency="USD",
    )
    return order, intent, p


def test_concurrent_capture_only_one_succeeds(order_with_one_item):
    """Two parallel captures on the same intent -> one CAPTURED, one no-op."""
    order, intent, _ = order_with_one_item
    results = []
    barrier = threading.Barrier(2)

    def capture(i):
        try:
            barrier.wait()
            res = services.capture_payment(
                intent_id=intent.id, external_id=f"ext-{i}"
            )
            return ("ok", res.status, res.external_id)
        except services.InvalidPaymentState as exc:
            return ("err", str(exc))
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(capture, range(2)))

    # Exactly one writer flips the state; the second sees CAPTURED with a
    # different external_id and raises InvalidPaymentState. The order
    # ends up PAID exactly once.
    intent.refresh_from_db()
    order.refresh_from_db()
    assert intent.status == PaymentIntent.CAPTURED
    assert order.status == Order.PAID
    customer.refresh_from_db()
    assert customer.wallet_balance == Decimal("100.00")

    successes = [r for r in results if r[0] == "ok"]
    assert len(successes) == 1


def test_duplicate_webhook_is_skipped(order_with_one_item):
    """Two identical webhooks -> the second is detected as duplicate."""
    _, intent, _ = order_with_one_item
    payload = {
        "event": "payment_captured",
        "intent_id": intent.id,
        "external_id": "ext-X",
    }

    first = services.process_webhook("sig-once", payload)
    second = services.process_webhook("sig-once", payload)

    assert first is True
    assert second is False  # deduplicated by UNIQUE constraint
    assert WebhookEvent.objects.filter(signature="sig-once").count() == 1
    intent.refresh_from_db()
    assert intent.status == PaymentIntent.CAPTURED


def test_capture_rejects_when_wallet_balance_is_insufficient(order_with_one_item):
    """Payment simulation rejects capture before stock/order state changes."""
    order, intent, product = order_with_one_item
    Customer.objects.filter(pk=order.customer_id).update(wallet_balance="50.00")

    with pytest.raises(services.InsufficientWalletBalance):
        services.capture_payment(intent_id=intent.id, external_id="ext-low-balance")

    intent.refresh_from_db()
    order.refresh_from_db()
    stock = StockItem.objects.get(product=product)
    customer = Customer.objects.get(pk=order.customer_id)

    assert intent.status == PaymentIntent.INIT
    assert order.status == Order.PENDING
    assert stock.reserved == 1
    assert customer.wallet_balance == Decimal("50.00")
