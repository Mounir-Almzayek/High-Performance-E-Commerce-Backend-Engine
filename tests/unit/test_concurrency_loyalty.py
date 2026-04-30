"""
Concurrency tests for the F-expression loyalty-points counter.

The Customer.loyalty_points field is updated WITHOUT a row lock - it
relies on Postgres's atomic single-statement UPDATE. This test proves
that 50 concurrent +1 increments yield exactly +50, with no lost
updates.

Lecture mapping:
  - Read-Modify-Write race -> avoided by pushing the math down to a
    single SQL statement.
  - "Lost update" demonstration -> if the implementation used Python-
    level read + write, we would see a count < 50 here.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import close_old_connections

from apps.users import services
from apps.users.models import Customer


pytestmark = pytest.mark.django_db(transaction=True)


def _run_parallel(fn, n: int) -> None:
    barrier = threading.Barrier(n)

    def wrapped(i):
        try:
            barrier.wait()
            fn(i)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=n) as ex:
        list(ex.map(wrapped, range(n)))


def test_no_lost_updates_under_50_concurrent_increments(customer):
    """50 concurrent +1 -> loyalty_points exactly +50."""
    Customer.objects.filter(pk=customer.pk).update(loyalty_points=0)

    def increment(_i):
        services.adjust_loyalty_points(customer.pk, +1)

    _run_parallel(increment, n=50)

    customer.refresh_from_db()
    assert customer.loyalty_points == 50, (
        f"lost updates detected: expected 50, got {customer.loyalty_points}"
    )


def test_cannot_go_negative_under_concurrent_subtracts(customer):
    """20 concurrent -1 on a balance of 5 -> exactly 5 succeed."""
    Customer.objects.filter(pk=customer.pk).update(loyalty_points=5)
    successes = []
    failures = []

    def deduct(_i):
        try:
            services.adjust_loyalty_points(customer.pk, -1)
            successes.append(1)
        except services.InsufficientLoyaltyPoints:
            failures.append(1)
        finally:
            close_old_connections()

    barrier = threading.Barrier(20)

    def runner(i):
        barrier.wait()
        deduct(i)

    with ThreadPoolExecutor(max_workers=20) as ex:
        list(ex.map(runner, range(20)))

    customer.refresh_from_db()
    assert customer.loyalty_points == 0
    assert len(successes) == 5
    assert len(failures) == 15
