"""
Inventory services - the core hot-spot of the entire project.

EVERY function in this module is a [NFR1] race-condition target and must be
implemented with explicit concurrency control. Reviewers should check that:

  - Each public function opens a transaction.
  - Each public function takes either a pessimistic row lock
    (select_for_update) OR uses an optimistic compare-and-set.
  - Each successful change writes a corresponding StockMovement row inside
    the same transaction (atomicity invariant - [NFR8]).

Public surface:

  reserve_stock(product_id, qty, reference)
      Reserve `qty` units for the given order/cart reference.
      Raises NotEnoughStock when available < qty.

  release_stock(product_id, qty, reference)
      Cancel a previous reservation.

  consume_stock(product_id, qty, reference)
      Convert a reservation into actual consumption (called after payment).

  restock(product_id, qty, reference)
      Inbound delivery from a supplier.

  bulk_reserve(items: list[(product_id, qty)], reference)
      Reserve several products atomically. MUST acquire row locks in a
      DETERMINISTIC ORDER (e.g. by product_id ASC) to prevent deadlocks
      when two concurrent carts share two products in opposite order.
"""


class NotEnoughStock(Exception):
    pass


def reserve_stock(*, product_id: int, qty: int, reference: str) -> None:
    """[NFR1 / NFR7] Atomic, lock-protected stock reservation."""
    # TODO [NFR1]: implement with either:
    #   - SELECT ... FOR UPDATE on stock_item.id, OR
    #   - optimistic update on stock_item.version,
    # Then INSERT a StockMovement(kind=RESERVE, quantity=qty).
    raise NotImplementedError("Concurrency owner must implement reserve_stock")


def release_stock(*, product_id: int, qty: int, reference: str) -> None:
    """Cancel a reservation - same locking discipline as reserve_stock."""
    # TODO [NFR1]
    raise NotImplementedError("Concurrency owner must implement release_stock")


def consume_stock(*, product_id: int, qty: int, reference: str) -> None:
    """Convert reserved -> consumed. Decrements both on_hand and reserved."""
    # TODO [NFR1]
    raise NotImplementedError("Concurrency owner must implement consume_stock")


def restock(*, product_id: int, qty: int, reference: str) -> None:
    """Inbound from supplier. Increments on_hand."""
    # TODO [NFR1]
    raise NotImplementedError("Concurrency owner must implement restock")


def bulk_reserve(*, items: list[tuple[int, int]], reference: str) -> None:
    """Multi-product reservation - lock rows in product_id order."""
    # TODO [NFR1]: deterministic-order locking to avoid deadlocks.
    raise NotImplementedError("Concurrency owner must implement bulk_reserve")
