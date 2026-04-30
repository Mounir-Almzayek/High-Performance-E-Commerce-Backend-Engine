"""
Concurrency primitives - [NFR1] shared-state protection.

Three mechanisms, picked per surface:

  1. Pessimistic row lock (`select_for_update`) - the default for hot
     rows in Postgres (StockItem, Order, PaymentIntent). Lives inside
     transaction.atomic() and serializes writers to the same row.

  2. Optimistic compare-and-set (`bump_version`) - for low-contention
     metadata (Product price, Customer profile). Lets readers proceed
     freely; writers retry on conflict.

  3. Distributed Redis lock (`distributed_lock`) - for resources without
     a canonical Postgres row that still need cross-instance mutual
     exclusion (cache warmer election, "send-once" tasks).

Only the Redis lock is implemented from scratch here; Postgres `FOR UPDATE`
and the F-expression update are first-class Django features wrapped only
for ergonomics.

Lecture references:
  - "Acquire / Process / Release" lifecycle  -> distributed_lock context manager
  - "Lost update" / "Read-Modify-Write" race -> bump_version (CAS) and
                                                 select_for_update wrappers
  - "Deadlock - Circular wait"               -> caller convention: lock rows
                                                in ascending PK order
                                                (enforced in
                                                apps.inventory.services
                                                .bulk_reserve)
"""
from __future__ import annotations

import functools
import logging
import random
import time
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterator, TypeVar

from django.db.models import F, Model, QuerySet
from django_redis import get_redis_connection

logger = logging.getLogger("core.concurrency")

T = TypeVar("T", bound=Model)


# ----------------------------- exceptions ---------------------------------


class StaleObjectError(Exception):
    """Optimistic update failed - another writer beat us to the row."""


class LockNotAcquired(Exception):
    """Distributed lock could not be acquired within the timeout."""


# ------------------------- Redis distributed lock -------------------------

# Compare-and-delete release script. Without this, the classic
# "lifted by another holder" race occurs:
#
#   T1 acquires lock with TTL=5s, but pauses (GC / disk stall) for 6s.
#   The TTL expires; T2 acquires the same key (legal, T1 lost it).
#   T1 wakes up and naively DELs the key -> deletes T2's lock.
#
# By tagging the value with a unique token and only deleting if the
# token still matches, we guarantee a holder can never lift someone
# else's lock.
_LUA_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


@contextmanager
def distributed_lock(
    key: str,
    timeout_ms: int = 5_000,
    *,
    blocking: bool = False,
    blocking_timeout_ms: int | None = None,
    retry_ms: int = 50,
) -> Iterator[None]:
    """Cross-instance mutex backed by Redis.

    The acquire path uses `SET key token NX PX timeout` which is atomic.
    The release path uses a Lua compare-and-delete so a holder cannot
    lift a lock now owned by someone else (see _LUA_RELEASE).

    Args:
        key: lock identifier, conventionally "lock:{resource_type}:{id}".
        timeout_ms: TTL on the lock - the upper bound on how long the
            holder may hold it before Redis auto-releases. Sized to the
            longest reasonable critical section, NOT to the full
            request budget.
        blocking: if False (default), raise LockNotAcquired immediately
            when the lock is held. If True, poll until acquire or until
            blocking_timeout_ms expires.
        blocking_timeout_ms: how long to wait when blocking. Defaults to
            timeout_ms.
        retry_ms: poll interval when blocking.

    Raises:
        LockNotAcquired: when the lock cannot be acquired in the allotted
            time. Callers should treat this as "system busy, try again
            later" (HTTP 503 or task retry), not as an internal error.
    """
    conn = get_redis_connection("default")
    token = uuid.uuid4().hex
    deadline = time.monotonic() + ((blocking_timeout_ms or timeout_ms) / 1000.0)

    while True:
        # SET ... NX PX ... is the canonical atomic acquire (Redis docs).
        if conn.set(key, token, nx=True, px=timeout_ms):
            break
        if not blocking or time.monotonic() >= deadline:
            raise LockNotAcquired(key)
        time.sleep(retry_ms / 1000.0)

    logger.debug("lock.acquired", extra={"key": key, "token": token[:8]})
    try:
        yield
    finally:
        try:
            conn.eval(_LUA_RELEASE, 1, key, token)
            logger.debug("lock.released", extra={"key": key, "token": token[:8]})
        except Exception:  # noqa: BLE001 - never lose the request because of release
            logger.exception("lock.release_failed", extra={"key": key})


# ------------------------- pessimistic helper -----------------------------


def select_for_update_or_skip(queryset: QuerySet) -> QuerySet:
    """Return the queryset locked with skip_locked=True.

    skip_locked is the right default for queue-style processing where a
    slow holder must NOT block siblings (head-of-line blocking). Callers
    that need FIFO fairness should call queryset.select_for_update()
    directly without this helper.

    NOTE: must be evaluated inside transaction.atomic(). Django silently
    drops select_for_update() outside a transaction.
    """
    return queryset.select_for_update(skip_locked=True)


# -------------------------- optimistic CAS --------------------------------


def bump_version(
    model_cls: type[T],
    pk: Any,
    expected_version: int,
    fields: dict[str, Any] | None = None,
) -> int:
    """Optimistic compare-and-set update on a row that has a `version` field.

    Issues a single SQL statement:

        UPDATE <table>
           SET version = version + 1, <fields>
         WHERE pk = <pk> AND version = <expected_version>

    The DB returns the affected row count; rowcount=0 means the version
    was bumped by another writer between our read and our update -
    classic "lost update" defended at the storage layer.

    Args:
        model_cls: the Django model class. Must declare a `version`
            PositiveIntegerField.
        pk: primary key of the row to update.
        expected_version: the value of `version` the caller saw on read.
        fields: additional column-value pairs to write in the same
            statement. F-expressions are accepted.

    Returns:
        The new version number on success.

    Raises:
        StaleObjectError: another writer changed `version` first. Caller
            should re-read and retry (use `with_optimistic_retry` for
            that).
    """
    update_kwargs: dict[str, Any] = dict(fields or {})
    update_kwargs["version"] = F("version") + 1
    rows = (
        model_cls._default_manager
        .filter(pk=pk, version=expected_version)
        .update(**update_kwargs)
    )
    if rows == 0:
        raise StaleObjectError(
            f"{model_cls.__name__}(pk={pk}) optimistic update failed: "
            f"version != {expected_version}"
        )
    return expected_version + 1


def with_optimistic_retry(
    retries: int = 3,
    backoff_ms: int = 10,
    max_backoff_ms: int = 200,
) -> Callable:
    """Decorator: retry a function on StaleObjectError with jittered backoff.

    Backoff doubles each attempt (10ms, 20ms, 40ms, ...) and is capped at
    max_backoff_ms. A small random jitter is added so concurrent losers
    do not collide on retry (the "thundering herd" effect).
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            for attempt in range(retries):
                try:
                    return fn(*args, **kwargs)
                except StaleObjectError:
                    if attempt == retries - 1:
                        raise
                    sleep_ms = min(
                        backoff_ms * (2 ** attempt) + random.randint(0, backoff_ms),
                        max_backoff_ms,
                    )
                    logger.info(
                        "optimistic.retry",
                        extra={
                            "fn": fn.__qualname__,
                            "attempt": attempt + 1,
                            "sleep_ms": sleep_ms,
                        },
                    )
                    time.sleep(sleep_ms / 1000.0)
            # unreachable, but satisfies type-checkers
            raise StaleObjectError("retry budget exhausted")  # pragma: no cover
        return wrapper
    return decorator
