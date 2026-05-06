"""
Resource governance - [NFR2].

Two layers of concurrency control:

  Outer layer (set in entrypoint.sh):
      Gunicorn workers x threads -> caps the number of concurrent HTTP
      requests being processed by a single instance.

  Inner layer (this module):
      A bounded semaphore that throttles fan-out work INSIDE a single
      request - e.g. when a checkout flow needs to hit several backends
      in parallel. Without this an unbounded ThreadPoolExecutor can
      saturate the database connection pool.

Public surface:

  - bounded_executor(max_workers=None) -> Context manager for ThreadPoolExecutor
        Returns a thread pool whose size is capped by
        settings.INTERNAL_POOL_MAX_CONCURRENCY when max_workers is None.

  - acquire_slot(resource: str, timeout: float = 1.0) -> bool
        Token-bucket / semaphore admission control around an external
        resource (DB, third-party API). Used to fail fast under overload
        instead of queueing forever.

  - get_pool_stats() -> dict
        Snapshot of in-flight tasks per resource for the diagnostics
        endpoint and the NFR10 report.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Iterator

from django.conf import settings

logger = logging.getLogger("core.resources")

# In-process tracking of in-flight operations per resource
# For cross-instance tracking, promote to Redis
_in_flight: dict[str, int] = defaultdict(int)
_in_flight_lock = threading.Lock()


@contextmanager
def bounded_executor(max_workers: int | None = None) -> Iterator[ThreadPoolExecutor]:
    """Cap-controlled executor context manager.

    Default cap is INTERNAL_POOL_MAX_CONCURRENCY. This ensures that even
    if many chunks are submitted, the thread pool won't exceed the DB
    connection budget.

    Args:
        max_workers: Max threads. If None, uses settings.INTERNAL_POOL_MAX_CONCURRENCY

    Yields:
        ThreadPoolExecutor with the specified max_workers

    Example:
        >>> with bounded_executor(max_workers=8) as executor:
        ...     futures = [executor.submit(process_chunk, chunk) for chunk in chunks]
    """
    cap = max_workers or settings.INTERNAL_POOL_MAX_CONCURRENCY

    # Use thread_name_prefix for identifiable threads in py-spy / logs
    executor = ThreadPoolExecutor(
        max_workers=cap,
        thread_name_prefix="nfr4_batch_worker",
    )

    logger.debug("bounded_executor.started", extra={"max_workers": cap})

    try:
        yield executor
    finally:
        # Shutdown waits for all pending futures to complete
        executor.shutdown(wait=True)
        logger.debug("bounded_executor.shutdown")


def acquire_slot(resource: str, timeout: float = 1.0) -> bool:
    """Best-effort admission control on a named resource.

    Uses an in-process BoundedSemaphore. If cross-instance fairness is needed,
    promote this to a Redis-backed implementation.

    Args:
        resource: Resource name (e.g., "payment_gateway", "inventory_api")
        timeout: Seconds to wait before returning False

    Returns:
        True if slot acquired, False if timeout reached
    """
    # Simple in-process implementation using a lock and counter
    # For production, use a proper token bucket or Redis-based semaphore
    start = time.monotonic()

    with _in_flight_lock:
        # Check if we're under the cap (using INTERNAL_POOL_MAX_CONCURRENCY as global cap)
        if _in_flight[resource] < settings.INTERNAL_POOL_MAX_CONCURRENCY:
            _in_flight[resource] += 1
            logger.debug("acquire_slot.success", extra={
                         "resource": resource, "in_flight": _in_flight[resource]})
            return True

    # If at capacity, wait briefly
    while time.monotonic() - start < timeout:
        time.sleep(0.01)  # 10ms polling
        with _in_flight_lock:
            if _in_flight[resource] < settings.INTERNAL_POOL_MAX_CONCURRENCY:
                _in_flight[resource] += 1
                logger.debug("acquire_slot.success_after_wait",
                             extra={"resource": resource})
                return True

    logger.warning("acquire_slot.timeout", extra={
                   "resource": resource, "timeout": timeout})
    return False


def release_slot(resource: str) -> None:
    """Release a slot acquired via acquire_slot.

    Args:
        resource: Resource name
    """
    with _in_flight_lock:
        if _in_flight[resource] > 0:
            _in_flight[resource] -= 1


def get_pool_stats() -> dict:
    """Returns {resource: in_flight_count} for diagnostics endpoint.

    Used by NFR5/NFR10 to monitor resource utilization.

    Returns:
        Dict mapping resource names to current in-flight counts
    """
    with _in_flight_lock:
        return dict(_in_flight)
