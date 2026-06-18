"""
Cart services.

Concurrency:
 - Multi-tab race on the same user's cart is solved by locking the
   Cart row at the start of every mutation (`SELECT ... FOR UPDATE`).
 - The `unique_together(cart, product)` constraint on CartItem makes
   the per-line update naturally idempotent: get_or_create + atomic
   UPDATE replaces the read-merge-write race with a single statement.

Once checkout starts, place_order acquires the same row lock, so any
in-flight cart mutation either completes before checkout snapshots the
items, or blocks until checkout commits and the cart is CHECKED_OUT
(and subsequent mutations fail the status guard).
"""
from __future__ import annotations

from django.db import transaction

from apps.products.models import Product
from core.aop.decorators import audit_log, timed

from .models import Cart, CartItem


# ----------------------------- exceptions ---------------------------------


class CartLocked(Exception):
    """Cart is no longer in OPEN status (already checked out)."""


# ----------------------------- public API ---------------------------------


def get_or_create_cart(customer) -> Cart:
    """Get or lazily create the customer's open cart.

    The demo data model keeps one cart per customer. After checkout the order
    has already snapshotted its items, so the same cart row can be reopened for
    the next shopping session.
    """
    cart, _ = Cart.objects.get_or_create(customer=customer)
    if cart.status != Cart.OPEN:
        CartItem.objects.filter(cart=cart).delete()
        cart.status = Cart.OPEN
        cart.save(update_fields=["status", "updated_at"])
    return cart


@timed("cart.add_item")
@audit_log("cart.add_item")
@transaction.atomic
def add_item(*, customer, product_id: int, quantity: int) -> CartItem:
    """Add (or merge) a line in the customer's open cart.

    Locks the cart row first, then upserts the item. Two concurrent
    adds for the same product serialize on the cart row -> the second
    caller observes the first caller's quantity and adds on top of it
    instead of overwriting.
    """
    cart = (
        Cart.objects
        .select_for_update()
        .select_related("customer")
        .get(customer=customer)
    )
    if cart.status != Cart.OPEN:
        raise CartLocked(f"Cart is in status={cart.status}; cannot mutate.")

    product = Product.objects.get(pk=product_id, status=Product.ACTIVE)
    item, created = CartItem.objects.get_or_create(
        cart=cart,
        product=product,
        defaults={"quantity": quantity, "unit_price": product.price},
    )
    if not created:
        # Atomic increment - safe because the cart row is locked.
        item.quantity = item.quantity + quantity
        item.save(update_fields=["quantity", "updated_at"])

    Cart.objects.filter(pk=cart.pk).update(version=cart.version + 1)
    return item


@timed("cart.update_item")
@audit_log("cart.update_item")
@transaction.atomic
def update_item(*, customer, item_id: int, quantity: int) -> CartItem | None:
    """Set the line quantity. quantity == 0 deletes the line.

    Locks the cart row first; the item itself is updated by primary key
    inside the cart-locked region, so no additional lock is needed.
    """
    cart = (
        Cart.objects
        .select_for_update()
        .get(customer=customer)
    )
    if cart.status != Cart.OPEN:
        raise CartLocked(f"Cart is in status={cart.status}; cannot mutate.")

    item = CartItem.objects.get(pk=item_id, cart=cart)
    if quantity == 0:
        item.delete()
        Cart.objects.filter(pk=cart.pk).update(version=cart.version + 1)
        return None

    item.quantity = quantity
    item.save(update_fields=["quantity", "updated_at"])
    Cart.objects.filter(pk=cart.pk).update(version=cart.version + 1)
    return item


@timed("cart.clear_cart")
def clear_cart(*, customer) -> None:
    """Empty the current cart while keeping it open for the next add."""
    cart = get_or_create_cart(customer)
    CartItem.objects.filter(cart=cart).delete()
    Cart.objects.filter(pk=cart.pk).update(status=Cart.OPEN)
