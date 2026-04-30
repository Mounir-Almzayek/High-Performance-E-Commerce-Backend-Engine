"""
Concurrency primitives - [NFR1] shared-state protection and [NFR7] locking.

Public surface (filled in by NFR1 / NFR7 owners):

  - distributed_lock(key, timeout=...) -> context manager
        Cross-instance mutex backed by Redis (SET NX PX). Required because
        web1 and web2 are separate processes - in-process threading.Lock
        is insufficient.

  - select_for_update_or_skip(qs)
        Helper around qs.select_for_update(skip_locked=True) so callers
        cannot forget skip_locked and accidentally serialize hot rows.

  - bump_version(instance)
        Helper for the optimistic-lock pattern - increments `version` on
        save and raises StaleObjectError if another writer beat us.

Hot call sites that MUST go through these helpers:
  - apps.inventory.services.decrement_stock
  - apps.orders.services.place_order
  - apps.payments.services.capture_payment
"""
from contextlib import contextmanager
from typing import Iterator


class StaleObjectError(Exception):
    """Raised when an optimistic update loses to a concurrent writer."""


@contextmanager
def distributed_lock(key: str, timeout_ms: int = 5_000) -> Iterator[None]:
    """Cross-instance mutex backed by Redis.

    NFR1 owner: implement using `django_redis.get_redis_connection()` with
    SET NX PX, and release with a Lua compare-and-delete to avoid lifting
    a lock we no longer own (the classic CAS-on-release pitfall).

    Reference key format: "lock:{resource_type}:{resource_id}".
    """
    # TODO [NFR1]: implement Redis-backed distributed lock.
    raise NotImplementedError("NFR1 owner must implement distributed_lock")
    yield  # pragma: no cover


def select_for_update_or_skip(queryset):
    """Pessimistic row lock that skips already-locked rows.

    NFR7 owner: implement and document why skip_locked is the safe default
    here (avoids head-of-line blocking under burst checkout traffic).
    """
    # TODO [NFR7]: return queryset.select_for_update(skip_locked=True)
    raise NotImplementedError("NFR7 owner must implement select_for_update_or_skip")


def bump_version(instance) -> None:
    """Optimistic-lock helper.

    NFR7 owner: implement the version-compare update inside an atomic block,
    raise StaleObjectError on rowcount=0, and document the retry policy.
    """
    # TODO [NFR7]: optimistic version bump
    raise NotImplementedError("NFR7 owner must implement bump_version")
