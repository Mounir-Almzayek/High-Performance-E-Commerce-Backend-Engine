"""NFR7 acceptance test for pessimistic inventory locking."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import close_old_connections

from apps.inventory import services
from apps.inventory.models import StockItem, StockMovement


pytestmark = pytest.mark.django_db(transaction=True)


def test_50_concurrent_reservations_do_not_oversell(product, monkeypatch):
    stock_item = StockItem.objects.create(
        product=product,
        on_hand=10,
        reserved=0,
        reorder_threshold=0,
    )
    monkeypatch.setattr(
        "apps.tasks.notifications.send_low_stock_alert.delay",
        lambda *_args, **_kwargs: None,
    )
    barrier = threading.Barrier(50)

    def reserve(worker_id: int):
        close_old_connections()
        try:
            barrier.wait()
            services.reserve_stock(
                product_id=product.id,
                qty=1,
                reference=f"nfr7-reserve-{worker_id}",
            )
            return None
        except Exception as exc:  # noqa: BLE001 - result is asserted below
            return exc
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=50) as executor:
        results = list(executor.map(reserve, range(50)))

    successes = [result for result in results if result is None]
    failures = [result for result in results if result is not None]

    assert len(successes) == 10
    assert len(failures) == 40
    assert all(isinstance(exc, services.NotEnoughStock) for exc in failures)

    stock_item.refresh_from_db()
    assert stock_item.reserved == 10
    assert stock_item.available == 0
    assert StockMovement.objects.filter(
        stock_item=stock_item,
        kind=StockMovement.RESERVE,
    ).count() == 10
    assert StockMovement.objects.count() == 10
    assert stock_item.reserved <= stock_item.on_hand
