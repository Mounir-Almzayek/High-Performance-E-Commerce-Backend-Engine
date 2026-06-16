"""NFR7 acceptance tests for optimistic Product updates."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from django.db import close_old_connections

from apps.products import services
from core.concurrency.locks import StaleObjectError


pytestmark = pytest.mark.django_db(transaction=True)


def test_correct_expected_version_updates_price_and_bumps_version(product):
    updated = services.update_product_price(
        product_id=product.id,
        new_price=Decimal("19.99"),
        expected_version=0,
    )

    assert updated.price == Decimal("19.99")
    assert updated.version == 1

    product.refresh_from_db()
    assert product.price == Decimal("19.99")
    assert product.version == 1


def test_stale_expected_version_raises_stale_object_error(product):
    services.update_product_price(
        product_id=product.id,
        new_price=Decimal("19.99"),
        expected_version=0,
    )

    with pytest.raises(StaleObjectError):
        services.update_product_price(
            product_id=product.id,
            new_price=Decimal("29.99"),
            expected_version=0,
        )

    product.refresh_from_db()
    assert product.price == Decimal("19.99")
    assert product.version == 1


def test_10_concurrent_updates_with_same_version_allow_one_winner(product):
    barrier = threading.Barrier(10)

    def update(worker_id: int):
        close_old_connections()
        try:
            barrier.wait()
            return services.update_product_price(
                product_id=product.id,
                new_price=Decimal(worker_id + 20),
                expected_version=0,
            )
        except Exception as exc:  # noqa: BLE001 - result is asserted below
            return exc
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(update, range(10)))

    successes = [result for result in results if not isinstance(result, Exception)]
    failures = [result for result in results if isinstance(result, Exception)]

    assert len(successes) == 1
    assert len(failures) == 9
    assert all(isinstance(exc, StaleObjectError) for exc in failures)

    product.refresh_from_db()
    assert product.version == 1
    assert product.price == successes[0].price
