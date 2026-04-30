"""
User services.

Why this file is interesting for NFR1: `adjust_loyalty_points` is the
canonical example of a mild-contention counter increment, and it is
intentionally implemented WITHOUT a row lock. Instead, it relies on
Postgres's atomic single-statement UPDATE with an F-expression:

    UPDATE customer SET loyalty_points = loyalty_points + <delta>
                       WHERE pk = <pk>

This SQL statement is atomic at the DB layer (one row-version write
under MVCC), so no application-level locking is needed and no
StaleObjectError can ever happen. It is faster than both pessimistic
locking (no lock contention) and optimistic CAS (no retry path).

Lecture mapping:
  - The "Bank Account problem" race is solved at the storage layer here:
    the Read-Modify-Write becomes a single atomic UPDATE, eliminating
    the interleaving window that produces lost updates.
  - This is the right tool ONLY for pure counter increments. For state
    machines (Order.status, PaymentIntent.status) we still need
    select_for_update because the new value depends on more than just
    the current value of the field.
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F

from core.aop.decorators import audit_log, timed

from .models import Customer

User = get_user_model()


# ----------------------------- exceptions ---------------------------------


class InsufficientLoyaltyPoints(Exception):
    """Cannot subtract more points than the customer has."""


# ----------------------------- public API ---------------------------------


@timed("users.register_customer")
@audit_log("users.register_customer")
@transaction.atomic
def register_customer(
    *, username: str, email: str, password: str, phone: str = ""
) -> Customer:
    """Create auth user + Customer profile atomically.

    [NFR8] If Customer creation fails, the User row rolls back too.
    """
    user = User.objects.create_user(username=username, email=email, password=password)
    return Customer.objects.create(user=user, phone=phone)


@timed("users.adjust_loyalty_points")
@audit_log("users.adjust_loyalty_points")
def adjust_loyalty_points(customer_id: int, delta: int) -> Customer:
    """Atomically add (or subtract) loyalty points.

    Uses an F-expression to push the read-modify-write down to the DB
    where it is one statement and inherently race-free. No application
    lock; no retries. The single-statement UPDATE is the cheapest
    correct solution for counter arithmetic.

    For strictly-non-negative semantics on subtraction, the WHERE clause
    additionally requires `loyalty_points >= -delta`; a 0-row update
    means the customer did not have enough points (no negative balance
    is ever written).
    """
    if delta == 0:
        return Customer.objects.get(pk=customer_id)

    if delta > 0:
        Customer.objects.filter(pk=customer_id).update(
            loyalty_points=F("loyalty_points") + delta,
            version=F("version") + 1,
        )
    else:
        # Conditional update: only succeed when balance >= |delta|.
        rows = (
            Customer.objects
            .filter(pk=customer_id, loyalty_points__gte=-delta)
            .update(
                loyalty_points=F("loyalty_points") + delta,
                version=F("version") + 1,
            )
        )
        if rows == 0:
            raise InsufficientLoyaltyPoints(
                f"customer={customer_id}: not enough points to deduct {-delta}"
            )

    return Customer.objects.get(pk=customer_id)
