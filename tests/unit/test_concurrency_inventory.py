"""
Concurrency tests for the inventory service.

These tests intentionally use threads + a real transactional database
to reproduce the race conditions the service is supposed to prevent.
Without the FOR UPDATE lock, every assertion in this file would fail.

Lecture mapping:
  - test_oversold_one_unit: the literal "Bank Account Problem" applied
    to inventory - two callers race for the last unit of stock.
  - test_bulk_reserve_no_deadlock: the "Circular wait" deadlock - two
    transactions touch products A and B in opposite order. Without our
    PK-ASC lock-acquisition rule this would deadlock and one
    transaction would be killed by Postgres's deadlock detector.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import close_old_connections

from apps.inventory import services
from apps.inventory.models import StockItem, StockMovement


pytestmark = pytest.mark.django_db(transaction=True)


def _run_concurrently(fn, n_threads: int) -> list:
    """Run `fn` on `n_threads` threads, returning the per-thread result.

    Each thread closes Django's thread-local DB connection on exit so
    subsequent tests start clean.
    """
    barrier = threading.Barrier(n_threads)

    def wrapped(i):
        try:
            barrier.wait()  # release all threads simultaneously
            return ("ok", fn(i))
        except Exception as exc:  # noqa: BLE001
            return ("err", exc)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        return list(ex.map(wrapped, range(n_threads)))


# --------------------- Race: oversold prevention ---------------------------


def test_oversold_one_unit(stock_item):
    """One unit of stock, ten concurrent reservers -> exactly one wins."""
    StockItem.objects.filter(pk=stock_item.pk).update(on_hand=1, reserved=0)

    def reserve(i):
        services.reserve_stock(
            product_id=stock_item.product_id, qty=1, reference=f"req-{i}"
        )

    results = _run_concurrently(reserve, n_threads=10)
    successes = [r for r in results if r[0] == "ok"]
    failures = [r for r in results if r[0] == "err"]

    assert len(successes) == 1, f"expected exactly 1 success, got {len(successes)}"
    assert len(failures) == 9
    for _, exc in failures:
        assert isinstance(exc, services.NotEnoughStock)

    stock_item.refresh_from_db()
    assert stock_item.reserved == 1
    assert stock_item.on_hand == 1
    assert StockMovement.objects.filter(
        stock_item=stock_item, kind=StockMovement.RESERVE
    ).count() == 1


def test_concurrent_reservations_consistent(stock_item):
    """20 reservers of 1 unit each on a stock of 5 -> exactly 5 succeed."""
    StockItem.objects.filter(pk=stock_item.pk).update(on_hand=5, reserved=0)

    def reserve(i):
        services.reserve_stock(
            product_id=stock_item.product_id, qty=1, reference=f"req-{i}"
        )

    results = _run_concurrently(reserve, n_threads=20)
    successes = [r for r in results if r[0] == "ok"]

    assert len(successes) == 5
    stock_item.refresh_from_db()
    assert stock_item.reserved == 5
    assert (
        StockMovement.objects.filter(
            stock_item=stock_item, kind=StockMovement.RESERVE
        ).count()
        == 5
    )


# --------------------- Race: deadlock avoidance ----------------------------


def test_bulk_reserve_no_deadlock(two_products):
    """Two threads bulk-reserve A+B in opposite orders -> no deadlock.

    Without our PK-ASC sort inside bulk_reserve, this test would
    intermittently fail with `django.db.utils.OperationalError: deadlock
    detected` from Postgres.
    """
    p1, p2 = two_products

    def t1(_):
        services.bulk_reserve(
            items=[(p1.id, 1), (p2.id, 1)], reference="t1"
        )

    def t2(_):
        # Submitted in opposite order on purpose.
        services.bulk_reserve(
            items=[(p2.id, 1), (p1.id, 1)], reference="t2"
        )

    results = []
    barrier = threading.Barrier(2)

    def runner(fn):
        try:
            barrier.wait()
            fn(None)
            return ("ok", None)
        except Exception as exc:
            return ("err", exc)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(runner, t1)
        f2 = ex.submit(runner, t2)
        results = [f1.result(), f2.result()]

    assert all(r[0] == "ok" for r in results), f"deadlock or other failure: {results}"

    StockItem.objects.get(product=p1).refresh_from_db()
    StockItem.objects.get(product=p2).refresh_from_db()
    assert StockItem.objects.get(product=p1).reserved == 2
    assert StockItem.objects.get(product=p2).reserved == 2


# --------------------- All-or-nothing on partial shortage ------------------


def test_bulk_reserve_partial_shortage_rolls_back(two_products):
    """If ANY product is short, NO reservation is written."""
    p1, p2 = two_products
    StockItem.objects.filter(product=p1).update(on_hand=10)
    StockItem.objects.filter(product=p2).update(on_hand=0)  # zero stock

    with pytest.raises(services.NotEnoughStock):
        services.bulk_reserve(items=[(p1.id, 1), (p2.id, 1)], reference="r")

    si_a = StockItem.objects.get(product=p1)
    si_b = StockItem.objects.get(product=p2)
    assert si_a.reserved == 0  # nothing committed for p1 either
    assert si_b.reserved == 0
    assert StockMovement.objects.count() == 0
