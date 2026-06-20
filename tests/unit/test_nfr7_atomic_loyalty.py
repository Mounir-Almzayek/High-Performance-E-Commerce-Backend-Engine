"""NFR7 acceptance tests for atomic loyalty-point counter updates."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import close_old_connections

from apps.users import services
from apps.users.models import Customer


pytestmark = pytest.mark.django_db(transaction=True)


def _run_concurrently(fn, workers: int) -> list:
    barrier = threading.Barrier(workers)

    def run(worker_id: int):
        close_old_connections()
        try:
            barrier.wait()
            return fn(worker_id)
        except Exception as exc:  # noqa: BLE001 - result is asserted by caller
            return exc
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(run, range(workers)))


def test_50_concurrent_increments_are_not_lost(customer):
    Customer.objects.filter(pk=customer.id).update(loyalty_points=0, version=0)

    results = _run_concurrently(
        lambda _worker_id: services.adjust_loyalty_points(customer.id, +1),
        workers=50,
    )

    assert not [result for result in results if isinstance(result, Exception)]

    customer.refresh_from_db()
    assert customer.loyalty_points == 50
    assert customer.version == 50


def test_50_concurrent_deductions_never_make_points_negative(customer):
    Customer.objects.filter(pk=customer.id).update(loyalty_points=10, version=0)

    results = _run_concurrently(
        lambda _worker_id: services.adjust_loyalty_points(customer.id, -1),
        workers=50,
    )
    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, Exception)]

    assert len(successes) == 10
    assert len(failures) == 40
    assert all(
        isinstance(exc, services.InsufficientLoyaltyPoints)
        for exc in failures
    )

    customer.refresh_from_db()
    assert customer.loyalty_points == 0
    assert customer.version == 10
