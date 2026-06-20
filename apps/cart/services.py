"""
Cart services.

Cart mutations lock the cart row first so multi-tab updates and checkout never
interleave incorrectly. NFR6 adds a cached cart read path; every mutation
invalidates the cart cache after commit.
"""
from __future__ import annotations

from django.db import transaction

from apps.products.models import Product
from core.aop.decorators import audit_log, timed
from core.cache.redis_cache import TTL_CART, cache_get_or_set, invalidate_cart

from .models import Cart, CartItem


class CartLocked(Exception):
    """Cart is no longer in OPEN status."""


def get_or_create_cart(customer) -> Cart:
    """Get or lazily create the customer's open cart."""
    cart, _ = Cart.objects.get_or_create(customer=customer)
    if cart.status != Cart.OPEN:
        CartItem.objects.filter(cart=cart).delete()
        cart.status = Cart.OPEN
        cart.save(update_fields=["status", "updated_at"])
    return cart


def get_cart_cached(customer) -> dict:
    """Return a serialisable cart representation from Redis."""
    from apps.cart.serializers import CartSerializer

    return cache_get_or_set(
        key=f"cart:{customer.pk}",
        builder=lambda: dict(CartSerializer(get_or_create_cart(customer)).data),
        ttl=TTL_CART,
    )


@timed("cart.add_item")
@audit_log("cart.add_item")
@transaction.atomic
def add_item(*, customer, product_id: int, quantity: int) -> CartItem:
    """Add or merge a line in the customer's open cart."""
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
        item.quantity = item.quantity + quantity
        item.save(update_fields=["quantity", "updated_at"])

    Cart.objects.filter(pk=cart.pk).update(version=cart.version + 1)
    transaction.on_commit(lambda uid=customer.pk: invalidate_cart(uid))
    return item


@timed("cart.update_item")
@audit_log("cart.update_item")
@transaction.atomic
def update_item(*, customer, item_id: int, quantity: int) -> CartItem | None:
    """Set a line quantity. quantity == 0 deletes the line."""
    cart = Cart.objects.select_for_update().get(customer=customer)
    if cart.status != Cart.OPEN:
        raise CartLocked(f"Cart is in status={cart.status}; cannot mutate.")

    item = CartItem.objects.get(pk=item_id, cart=cart)
    if quantity == 0:
        item.delete()
        Cart.objects.filter(pk=cart.pk).update(version=cart.version + 1)
        transaction.on_commit(lambda uid=customer.pk: invalidate_cart(uid))
        return None

    item.quantity = quantity
    item.save(update_fields=["quantity", "updated_at"])
    Cart.objects.filter(pk=cart.pk).update(version=cart.version + 1)
    transaction.on_commit(lambda uid=customer.pk: invalidate_cart(uid))
    return item


@timed("cart.clear_cart")
@transaction.atomic
def clear_cart(*, customer) -> None:
    """Empty the current cart while keeping it open for the next add."""
    cart = Cart.objects.select_for_update().get(customer=customer)
    CartItem.objects.filter(cart=cart).delete()
    Cart.objects.filter(pk=cart.pk).update(status=Cart.OPEN, version=cart.version + 1)
    transaction.on_commit(lambda uid=customer.pk: invalidate_cart(uid))
