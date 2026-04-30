"""
Cart services.

Concurrency considerations:
 - The same user might hold the cart open in multiple tabs / devices.
 - Add and remove operations on the same product race on CartItem.quantity.
 - Once the user clicks checkout, no further mutation must succeed.

Implementation must take a row lock on the Cart row (or use the optimistic
`version` column) before mutating items. [NFR1] / [NFR7]
"""
from django.db import transaction

from apps.products.models import Product

from .models import Cart, CartItem


def get_or_create_cart(customer) -> Cart:
    cart, _ = Cart.objects.get_or_create(customer=customer)
    return cart


def add_item(*, customer, product_id: int, quantity: int) -> CartItem:
    """Add or merge a line in the user's open cart.

    [NFR1] Must serialize per-cart so two concurrent "add" calls cannot
    create duplicate rows or corrupt the quantity.
    """
    # TODO [NFR1]: lock the Cart row (or use distributed lock keyed by cart
    #              id), then upsert the CartItem inside the same transaction.
    with transaction.atomic():
        cart = Cart.objects.select_related("customer").get(customer=customer)
        product = Product.objects.get(pk=product_id, status=Product.ACTIVE)
        item, created = CartItem.objects.get_or_create(
            cart=cart, product=product,
            defaults={"quantity": quantity, "unit_price": product.price},
        )
        if not created:
            item.quantity = item.quantity + quantity
            item.save(update_fields=["quantity", "updated_at"])
        return item


def update_item(*, customer, item_id: int, quantity: int) -> CartItem | None:
    """Set quantity. quantity == 0 deletes the line."""
    # TODO [NFR1]: lock the cart, then update or delete.
    raise NotImplementedError("Concurrency owner must implement update_item")


def clear_cart(*, customer) -> None:
    """Wipe the open cart (post-checkout cleanup)."""
    Cart.objects.filter(customer=customer).update(status=Cart.CHECKED_OUT)
