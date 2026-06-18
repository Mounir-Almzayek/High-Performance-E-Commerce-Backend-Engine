import logging
import sys
import time
from decimal import Decimal

from django.contrib.auth import get_user_model

from apps.cart import services as cart_services
from apps.cart.models import Cart
from apps.inventory import services as inventory_services
from apps.inventory.models import StockItem, StockMovement
from apps.orders import services as order_services
from apps.orders.models import Order, OrderItem
from apps.payments import services as payment_services
from apps.payments.models import PaymentIntent
from apps.products.models import Category, Product
from apps.users.models import Address, Customer

User = get_user_model()


def _create_customer(prefix):
    user = User.objects.create_user(username=f"{prefix}-user", password="x")
    customer = Customer.objects.create(user=user, wallet_balance="500.00")
    address = Address.objects.create(
        customer=customer,
        kind=Address.SHIPPING,
        line1="NFR8 test",
        city="NFR8",
        postal_code="00000",
        country="US",
        is_default=True,
    )
    return customer, address


def _create_product(prefix, price="100.00", on_hand=10, reserved=0):
    category, _ = Category.objects.get_or_create(
        slug="nfr8-test",
        defaults={"name": "NFR8 Test Category"},
    )
    product = Product.objects.create(
        sku=f"{prefix}-sku",
        name=f"{prefix} Product",
        slug=f"{prefix}-product",
        category=category,
        price=price,
    )
    stock = StockItem.objects.create(
        product=product,
        on_hand=on_hand,
        reserved=reserved,
        reorder_threshold=0,
    )
    return product, stock


def run_test(output_path="tools/locust/results/nfr8-atomicity-test.txt"):
    logging.getLogger("core.aop").disabled = True
    logging.getLogger("core.transactions").disabled = True
    prefix = f"nfr8-{int(time.time())}"

    with open(output_path, "w", encoding="utf8") as out:
        def log(*args):
            txt = " ".join(str(a) for a in args)
            print(txt)
            out.write(txt + "\n")

        log("Starting NFR8 ACID rollback evidence test")

        # Scenario 1: place_order fails after Order/OrderItems are inserted.
        customer, address = _create_customer(f"{prefix}-order")
        product, stock = _create_product(f"{prefix}-order", on_hand=10, reserved=0)
        cart_services.get_or_create_cart(customer)
        cart_services.add_item(customer=customer, product_id=product.id, quantity=2)

        before_orders = Order.objects.filter(customer=customer).count()
        before_order_items = OrderItem.objects.filter(order__customer=customer).count()
        before_movements = StockMovement.objects.filter(stock_item=stock).count()
        before_cart_status = Cart.objects.get(customer=customer).status
        stock.refresh_from_db()

        log("--- Scenario 1: place_order failure injection ---")
        log("Injected failure point: inventory bulk_reserve, after Order and OrderItems are created inside the transaction")
        log(f"Before Orders: {before_orders}")
        log(f"Before OrderItems: {before_order_items}")
        log(f"Before StockMovements: {before_movements}")
        log(f"Before cart status: {before_cart_status}")
        log(f"Before stock on_hand/reserved/available: {stock.on_hand}/{stock.reserved}/{stock.available}")

        real_bulk_reserve = inventory_services.bulk_reserve

        def fail_bulk_reserve(**kwargs):
            raise RuntimeError("injected NFR8 reservation failure")

        inventory_services.bulk_reserve = fail_bulk_reserve
        place_order_failed = False
        try:
            order_services.place_order(
                customer=customer,
                shipping_address_id=address.id,
                billing_address_id=address.id,
            )
        except RuntimeError as exc:
            place_order_failed = "injected NFR8 reservation failure" in str(exc)
            log(f"Caught expected exception: {exc}")
        finally:
            inventory_services.bulk_reserve = real_bulk_reserve

        stock.refresh_from_db()
        cart = Cart.objects.get(customer=customer)
        after_orders = Order.objects.filter(customer=customer).count()
        after_order_items = OrderItem.objects.filter(order__customer=customer).count()
        after_movements = StockMovement.objects.filter(stock_item=stock).count()

        log(f"After Orders: {after_orders}")
        log(f"After OrderItems: {after_order_items}")
        log(f"After StockMovements: {after_movements}")
        log(f"After cart status: {cart.status}")
        log(f"After stock on_hand/reserved/available: {stock.on_hand}/{stock.reserved}/{stock.available}")

        scenario_1_passed = (
            place_order_failed
            and after_orders == before_orders
            and after_order_items == before_order_items
            and after_movements == before_movements
            and cart.status == before_cart_status
            and stock.on_hand == 10
            and stock.reserved == 0
        )
        log(f"Scenario 1 verdict: {'PASS' if scenario_1_passed else 'FAIL'}")

        # Scenario 2: capture_payment fails after consume_stock really writes.
        customer2, address2 = _create_customer(f"{prefix}-payment")
        customer2.wallet_balance = Decimal("200.00")
        customer2.save(update_fields=["wallet_balance"])
        product2, stock2 = _create_product(f"{prefix}-payment", on_hand=5, reserved=1)
        order = Order.objects.create(
            customer=customer2,
            shipping_address=address2,
            billing_address=address2,
            subtotal="100.00",
            tax="0.00",
            shipping_fee="0.00",
            total="100.00",
            currency="USD",
        )
        OrderItem.objects.create(
            order=order,
            product=product2,
            product_sku=product2.sku,
            product_name=product2.name,
            unit_price="100.00",
            quantity=1,
            line_total="100.00",
        )
        intent = PaymentIntent.objects.create(order=order, amount="100.00", currency="USD")

        before_wallet = Customer.objects.get(pk=customer2.pk).wallet_balance
        before_intent_status = intent.status
        before_order_status = order.status
        before_payment_movements = StockMovement.objects.filter(stock_item=stock2).count()
        stock2.refresh_from_db()

        log("--- Scenario 2: capture_payment late failure injection ---")
        log("Injected failure point: after consume_stock has written StockItem + StockMovement inside the transaction")
        log(f"Before wallet balance: {before_wallet}")
        log(f"Before PaymentIntent status: {before_intent_status}")
        log(f"Before Order status: {before_order_status}")
        log(f"Before StockMovements: {before_payment_movements}")
        log(f"Before stock on_hand/reserved/available: {stock2.on_hand}/{stock2.reserved}/{stock2.available}")

        real_consume_stock = inventory_services.consume_stock

        def consume_then_fail(**kwargs):
            real_consume_stock(**kwargs)
            raise RuntimeError("injected NFR8 failure after stock consume")

        inventory_services.consume_stock = consume_then_fail
        capture_failed = False
        try:
            payment_services.capture_payment(intent_id=intent.id, external_id=f"{prefix}-ext")
        except RuntimeError as exc:
            capture_failed = "injected NFR8 failure after stock consume" in str(exc)
            log(f"Caught expected exception: {exc}")
        finally:
            inventory_services.consume_stock = real_consume_stock

        customer2.refresh_from_db()
        intent.refresh_from_db()
        order.refresh_from_db()
        stock2.refresh_from_db()
        after_payment_movements = StockMovement.objects.filter(stock_item=stock2).count()

        log(f"After wallet balance: {customer2.wallet_balance}")
        log(f"After PaymentIntent status: {intent.status}")
        log(f"After Order status: {order.status}")
        log(f"After StockMovements: {after_payment_movements}")
        log(f"After stock on_hand/reserved/available: {stock2.on_hand}/{stock2.reserved}/{stock2.available}")

        scenario_2_passed = (
            capture_failed
            and customer2.wallet_balance == before_wallet
            and intent.status == before_intent_status
            and order.status == before_order_status
            and after_payment_movements == before_payment_movements
            and stock2.on_hand == 5
            and stock2.reserved == 1
        )
        log(f"Scenario 2 verdict: {'PASS' if scenario_2_passed else 'FAIL'}")

        log("--- Final verdict ---")
        passed = scenario_1_passed and scenario_2_passed
        if passed:
            log("PASS: Injected partial failures rolled back the full composite operations.")
        else:
            log("FAIL: At least one composite operation left partial state behind.")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(run_test())
