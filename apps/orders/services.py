"""
Order services - the canonical multi-step composite write of the system.

`place_order` is wrapped in a single `transaction.atomic()` so it is
all-or-nothing (NFR8 invariant). The Cart row is locked FIRST to
serialize concurrent checkouts from the same user (multi-tab race);
inventory rows are then locked in PK ASC order via
`apps.inventory.services.bulk_reserve` (deadlock avoidance, NFR1).

Async dispatch (invoice, notifications) is intentionally LEFT to the
NFR3 owner. When that lands, the dispatch must use
`transaction.on_commit(...)` so a rolled-back order can never produce
an orphan email or PDF.

Lecture references:
  - "Bank Account Problem" (Session 1) -> we lock the Cart row at the
    start of the transaction so no concurrent caller can read the same
    items, decrement stock against them, and produce a phantom order.
  - "Critical section" -> totals are computed BEFORE the inventory lock
    is taken; only the reservation + inserts live inside the locked
    section.
  - "Deadlock" -> bulk_reserve enforces global PK-ASC lock order.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction

from apps.cart.models import Cart, CartItem
from apps.inventory import services as inventory_services
from apps.users.models import Address, Customer
from core.aop.decorators import audit_log, timed

from .models import Order, OrderItem


# ----------------------------- exceptions ---------------------------------


class CartEmpty(Exception):
    """Cannot place an order from an empty cart."""


class InvalidOrderState(Exception):
    """Order is not in a state that allows the requested transition."""


# ----------------------------- helpers ------------------------------------


def _calculate_totals(items: list[CartItem]) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    subtotal = sum((i.unit_price * i.quantity for i in items), start=Decimal("0"))
    tax = (subtotal * Decimal("0.15")).quantize(Decimal("0.01"))
    shipping_fee = Decimal("5.00") if subtotal < Decimal("100") else Decimal("0")
    total = subtotal + tax + shipping_fee
    return subtotal, tax, shipping_fee, total


# ----------------------------- public API ---------------------------------


@timed("orders.place_order")
@audit_log("orders.place_order")
@transaction.atomic
def place_order(
    *,
    customer: Customer,
    shipping_address_id: int,
    billing_address_id: int,
) -> Order:
    """Create a pending order from the customer's open cart.

    Steps (all inside one transaction):

      1. Lock the Cart row exclusively (FOR UPDATE). Two tabs of the
         same user clicking checkout will serialize here.
      2. Snapshot the cart items and recompute totals.
      3. Resolve and validate the addresses.
      4. INSERT Order, INSERT OrderItems (snapshot prices/sku/name).
      5. inventory.bulk_reserve(...) - holds row locks on every product
         in ASC order, validates availability, reserves units, writes
         StockMovement rows. Raises NotEnoughStock to roll the whole
         transaction back if any product is short.
      6. Mark cart as CHECKED_OUT.
      7. (NFR3 owner): wire transaction.on_commit(...) for invoice +
         notification dispatch.

    Returns the persisted Order.
    """
    # Step 1: lock the cart row.
    cart = (
        Cart.objects
        .select_for_update()
        .select_related("customer")
        .get(customer=customer, status=Cart.OPEN)
    )

    # Step 2: snapshot items.
    items = list(cart.items.select_related("product"))
    if not items:
        raise CartEmpty("Cannot place an order with an empty cart.")

    # Step 3: validate addresses up front (no point in locking inventory
    # if the addresses are wrong - fail fast outside any heavy work).
    shipping = Address.objects.get(pk=shipping_address_id, customer=customer)
    billing = Address.objects.get(pk=billing_address_id, customer=customer)

    subtotal, tax, shipping_fee, total = _calculate_totals(items)

    # Step 4: persist order header + lines (snapshots).
    order = Order.objects.create(
        customer=customer,
        shipping_address=shipping,
        billing_address=billing,
        subtotal=subtotal,
        tax=tax,
        shipping_fee=shipping_fee,
        total=total,
        currency="USD",
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

    # Step 5: reserve inventory (PK-ASC ordered, deadlock-free).
    inventory_services.bulk_reserve(
        items=[(i.product_id, i.quantity) for i in items],
        reference=str(order.public_id),
    )

    # Step 6: close out the cart.
    Cart.objects.filter(pk=cart.pk).update(status=Cart.CHECKED_OUT)

    # Step 7: NFR3 owner adds:
    #   transaction.on_commit(lambda: invoicing.generate_invoice.delay(order.id))
    #   transaction.on_commit(lambda: notifications.send_order_confirmation.delay(order.id))

    return order


@timed("orders.cancel_order")
@audit_log("orders.cancel_order")
@transaction.atomic
def cancel_order(*, order: Order, reason: str = "") -> Order:
    """Cancel a PENDING order and release its inventory reservations.

    Locks the order row before transitioning. The status guard inside
    the lock prevents the foreground cancel from racing with a webhook
    that is concurrently flipping the order to PAID.
    """
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.status != Order.PENDING:
        raise InvalidOrderState(
            f"Cannot cancel order in status={locked.status}"
        )

    # Release reservations - lock rows in PK ASC order to avoid deadlock
    # with concurrent capture / cancel flows on overlapping products.
    for item in OrderItem.objects.filter(order=locked).order_by("product_id"):
        inventory_services.release_stock(
            product_id=item.product_id,
            qty=item.quantity,
            reference=str(locked.public_id),
        )

    Order.objects.filter(pk=locked.pk).update(
        status=Order.CANCELLED,
        version=locked.version + 1,
    )
    locked.refresh_from_db(fields=["status", "version", "updated_at"])
    return locked
