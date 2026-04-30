"""
User services - business logic isolated from views.

NFR ownership inside this module:
 - register_customer .................... [NFR8] composite write
 - adjust_loyalty_points ................ [NFR1] / [NFR7] race condition
"""
from django.contrib.auth import get_user_model
from django.db import transaction

from .models import Customer

User = get_user_model()


def register_customer(*, username: str, email: str, password: str, phone: str = "") -> Customer:
    """Create auth user + Customer profile atomically.

    [NFR8] If Customer creation fails, the User row must be rolled back too.
    """
    with transaction.atomic():
        user = User.objects.create_user(username=username, email=email, password=password)
        return Customer.objects.create(user=user, phone=phone)


def adjust_loyalty_points(customer_id: int, delta: int) -> Customer:
    """Add (or subtract) loyalty points safely under concurrent updates.

    [NFR1] Several flows can race here (order paid, refund issued, admin tweak).
    [NFR7] Implementation must use either:
              - SELECT ... FOR UPDATE on Customer.id, OR
              - optimistic update on Customer.version
           and document the choice in docs/requirements/07-*.md.
    """
    # TODO [NFR1 / NFR7]: implement concurrency-safe update.
    raise NotImplementedError("Concurrency owner must implement adjust_loyalty_points")
