"""
Order services - the system's most demanding write path.

`place_order` is the canonical multi-step transaction. It MUST satisfy:

  [NFR1] Every concurrent caller sees a consistent view of stock.
  [NFR3] Side effects (invoice, notifications) are dispatched ASYNC, after
         commit, never inside the request transaction.
  [NFR7] The order is saved with version=0; subsequent updates use
         optimistic locking.
  [NFR8] All-or-nothing: if any step fails (stock reservation, address
         lookup, total computation), the transaction rolls back and no
         StockMovement / Order rows persist.

`cancel_order` releases reserved stock and transitions the order. It races
with payment webhooks and must follow the same locking discipline.
"""
from decimal import Decimal

from django.db import transaction

from apps.cart.models import Cart, CartItem
from apps.cart.services import clear_cart
from apps.inventory import services as inventory_services
from apps.users.models import Address, Customer

from .models import Order, OrderItem


class CartEmpty(Exception):
    pass


def _calculate_totals(items: list[CartItem]) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    subtotal = sum((i.unit_price * i.quantity for i in items), start=Decimal("0"))
    tax = (subtotal * Decimal("0.15")).quantize(Decimal("0.01"))
    shipping_fee = Decimal("5.00") if subtotal < Decimal("100") else Decimal("0")
    total = subtotal + tax + shipping_fee
    return subtotal, tax, shipping_fee, total


def place_order(
    *,
    customer: Customer,
    shipping_address_id: int,
    billing_address_id: int,
) -> Order:
    """Create a pending order from the user's cart.

    Reference flow (the NFR1 / NFR8 owner finishes the locking + async
    dispatch parts marked TODO below):

      1. Open transaction.
      2. Lock the Cart row.
      3. Snapshot CartItems and compute totals.
      4. inventory_services.bulk_reserve(items, reference=order.public_id)
      5. Insert Order + OrderItems.
      6. Mark cart as CHECKED_OUT.
      7. Defer async work to AFTER commit (invoicing, notification).
    """
    # TODO [NFR1 + NFR8]: complete the locking and post-commit dispatch.
    with transaction.atomic():
        cart = Cart.objects.select_related("customer").get(customer=customer, status=Cart.OPEN)
        items = list(cart.items.select_related("product"))
        if not items:
            raise CartEmpty("Cannot place an order with an empty cart.")

        shipping = Address.objects.get(pk=shipping_address_id, customer=customer)
        billing = Address.objects.get(pk=billing_address_id, customer=customer)

        subtotal, tax, shipping_fee, total = _calculate_totals(items)

        order = Order.objects.create(
            customer=customer,
            shipping_address=shipping,
            billing_address=billing,
            subtotal=subtotal,
            tax=tax,
            shipping_fee=shipping_fee,
            total=total,
        )
        OrderItem.objects.bulk_create([
            OrderItem(
                order=order,
                product=i.product,
                product_sku=i.product.sku,
                product_name=i.product.name,
                unit_price=i.unit_price,
                quantity=i.quantity,
                line_total=i.unit_price * i.quantity,
            )
            for i in items
        ])

        # TODO [NFR1]: replace with bulk_reserve once the inventory owner
        #              implements it. Today the call below raises.
        inventory_services.bulk_reserve(
            items=[(i.product_id, i.quantity) for i in items],
            reference=str(order.public_id),
        )

        clear_cart(customer=customer)

        # TODO [NFR3]: dispatch tasks via transaction.on_commit:
        #   - tasks.invoicing.generate_invoice.delay(order.id)
        #   - tasks.notifications.send_order_confirmation.delay(order.id)
        return order


def cancel_order(*, order: Order, reason: str = "") -> Order:
    """Cancel a pending order and release its reservations.

    [NFR1] Locks the order row and races with payment-capture webhooks.
    """
    # TODO [NFR1 + NFR7]: lock + status-transition + release reservations.
    raise NotImplementedError("Concurrency owner must implement cancel_order")
