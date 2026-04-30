"""
Inventory services - the highest-contention surface in the system.

All five public functions take a PESSIMISTIC row-level lock on
StockItem (`SELECT ... FOR UPDATE`) inside `transaction.atomic()`. The
StockMovement audit row is INSERTed in the same transaction so the
ledger can never desync from on_hand / reserved.

Why pessimistic and not optimistic on this surface:
  - Contention is HIGH on a small set of hot rows (flash-sale products).
  - Optimistic CAS would thrash with retries; pessimistic serializes
    the writers cheaply at the DB layer.
  - The work inside the lock is one row update + one row insert -
    micro-second order - so the critical section is tiny and lock hold
    time is bounded.

Lecture references:
  - Race condition / Read-Modify-Write -> reserved += qty was a 3-step
    sequence; FOR UPDATE collapses it into a single serialized op.
  - Lost update problem (the "Bank Account" example) -> cannot occur
    here because the lock is held across the entire RMW.
  - Deadlock / Circular wait -> bulk_reserve sorts product_ids ASC
    before locking, so two concurrent carts that share two products in
    opposite order acquire locks in the SAME order and cannot deadlock.
  - Critical section sizing -> validation (qty > 0) happens BEFORE the
    lock; only the read-modify-write lives inside it.
"""
from __future__ import annotations

from django.db import transaction
from django.db.models import F

from core.aop.decorators import audit_log, timed

from .models import StockItem, StockMovement


# ----------------------------- exceptions ---------------------------------


class NotEnoughStock(Exception):
    """Requested quantity exceeds available stock."""


class InvalidQuantity(ValueError):
    """Quantity must be a positive integer."""


# ----------------------------- helpers ------------------------------------


def _check_qty(qty: int) -> None:
    if not isinstance(qty, int) or qty <= 0:
        raise InvalidQuantity(f"qty must be a positive int, got {qty!r}")


def _lock_one(product_id: int) -> StockItem:
    """Return the StockItem with an exclusive row lock.

    MUST be called inside transaction.atomic() - Django silently drops
    select_for_update() outside an atomic block.
    """
    return (
        StockItem.objects
        .select_for_update()
        .select_related("product")
        .get(product_id=product_id)
    )


# ----------------------------- public API ---------------------------------


@timed("inventory.reserve_stock")
@audit_log("inventory.reserve_stock")
@transaction.atomic
def reserve_stock(*, product_id: int, qty: int, reference: str) -> None:
    """Reserve `qty` units of `product_id` for the given reference.

    Race-free under concurrent callers: the FOR UPDATE lock serializes
    everyone competing for the same StockItem row.
    """
    _check_qty(qty)
    si = _lock_one(product_id)
    if si.available < qty:
        raise NotEnoughStock(
            f"product_id={product_id}: requested {qty}, available {si.available}"
        )
    StockItem.objects.filter(pk=si.pk).update(
        reserved=F("reserved") + qty,
        version=F("version") + 1,
    )
    StockMovement.objects.create(
        stock_item=si,
        kind=StockMovement.RESERVE,
        quantity=qty,
        reference=reference,
    )


@timed("inventory.release_stock")
@audit_log("inventory.release_stock")
@transaction.atomic
def release_stock(*, product_id: int, qty: int, reference: str) -> None:
    """Cancel a previous reservation. Lowers `reserved` by qty."""
    _check_qty(qty)
    si = _lock_one(product_id)
    if si.reserved < qty:
        raise InvalidQuantity(
            f"product_id={product_id}: cannot release {qty}; "
            f"only {si.reserved} reserved"
        )
    StockItem.objects.filter(pk=si.pk).update(
        reserved=F("reserved") - qty,
        version=F("version") + 1,
    )
    StockMovement.objects.create(
        stock_item=si,
        kind=StockMovement.RELEASE,
        quantity=-qty,
        reference=reference,
    )


@timed("inventory.consume_stock")
@audit_log("inventory.consume_stock")
@transaction.atomic
def consume_stock(*, product_id: int, qty: int, reference: str) -> None:
    """Convert reserved units to consumed (on payment capture).

    Decrements both `on_hand` and `reserved` atomically.
    """
    _check_qty(qty)
    si = _lock_one(product_id)
    if si.reserved < qty or si.on_hand < qty:
        raise InvalidQuantity(
            f"product_id={product_id}: cannot consume {qty} "
            f"(on_hand={si.on_hand}, reserved={si.reserved})"
        )
    StockItem.objects.filter(pk=si.pk).update(
        reserved=F("reserved") - qty,
        on_hand=F("on_hand") - qty,
        version=F("version") + 1,
    )
    StockMovement.objects.create(
        stock_item=si,
        kind=StockMovement.CONSUME,
        quantity=-qty,
        reference=reference,
    )


@timed("inventory.restock")
@audit_log("inventory.restock")
@transaction.atomic
def restock(*, product_id: int, qty: int, reference: str) -> None:
    """Inbound delivery from a supplier. Raises `on_hand` by qty."""
    _check_qty(qty)
    si = _lock_one(product_id)
    StockItem.objects.filter(pk=si.pk).update(
        on_hand=F("on_hand") + qty,
        version=F("version") + 1,
    )
    StockMovement.objects.create(
        stock_item=si,
        kind=StockMovement.INBOUND,
        quantity=qty,
        reference=reference,
    )


@timed("inventory.bulk_reserve")
@audit_log("inventory.bulk_reserve")
@transaction.atomic
def bulk_reserve(*, items: list[tuple[int, int]], reference: str) -> None:
    """Reserve several products inside a single atomic transaction.

    Locks rows in ASCENDING product_id order to prevent the classic
    circular-wait deadlock that arises when two transactions hold one
    row each and want the other:

        T1: holds A, waits for B
        T2: holds B, waits for A      <- deadlock

    Sorting the lock-acquisition order globally guarantees that any two
    concurrent transactions touching the same rows acquire them in the
    SAME order, eliminating the cycle.

    Args:
        items: list of (product_id, qty) pairs.
        reference: free-form correlation tag (e.g. an order public_id).

    Raises:
        NotEnoughStock: if ANY product cannot satisfy its qty (the whole
            transaction rolls back - all-or-nothing).
        InvalidQuantity: if any qty <= 0.
    """
    if not items:
        return
    for _, qty in items:
        _check_qty(qty)

    # 1. Sort by product_id to enforce a global lock-acquisition order.
    sorted_items = sorted(items, key=lambda x: x[0])
    product_ids = [pid for pid, _ in sorted_items]

    # 2. Acquire all row locks in one query. ORDER BY product_id makes
    #    Postgres scan rows (and acquire locks) in our chosen order.
    locked = list(
        StockItem.objects
        .select_for_update()
        .filter(product_id__in=product_ids)
        .order_by("product_id")
    )
    by_pid = {si.product_id: si for si in locked}

    # 3. Validate availability BEFORE writing anything. All-or-nothing.
    for pid, qty in sorted_items:
        si = by_pid.get(pid)
        if si is None:
            raise NotEnoughStock(f"product_id={pid}: no stock record")
        if si.available < qty:
            raise NotEnoughStock(
                f"product_id={pid}: requested {qty}, available {si.available}"
            )

    # 4. Apply updates and the audit ledger inside the same transaction.
    movements: list[StockMovement] = []
    for pid, qty in sorted_items:
        si = by_pid[pid]
        StockItem.objects.filter(pk=si.pk).update(
            reserved=F("reserved") + qty,
            version=F("version") + 1,
        )
        movements.append(StockMovement(
            stock_item=si,
            kind=StockMovement.RESERVE,
            quantity=qty,
            reference=reference,
        ))
    StockMovement.objects.bulk_create(movements)
