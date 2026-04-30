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

Public surface (filled in by NFR2 owner):

  - bounded_executor(max_workers=None) -> ThreadPoolExecutor
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
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings


def bounded_executor(max_workers: int | None = None) -> ThreadPoolExecutor:
    """Cap-controlled executor. Default cap is INTERNAL_POOL_MAX_CONCURRENCY."""
    # TODO [NFR2]: build the executor, apply the cap, and add a name_prefix
    #              so threads are identifiable in py-spy / logs.
    cap = max_workers or settings.INTERNAL_POOL_MAX_CONCURRENCY
    raise NotImplementedError(f"NFR2 owner must implement bounded_executor (cap={cap})")


def acquire_slot(resource: str, timeout: float = 1.0) -> bool:
    """Best-effort admission control on a named resource."""
    # TODO [NFR2]: implement a per-resource semaphore (in-process or Redis-
    #              backed if cross-instance fairness is needed).
    raise NotImplementedError("NFR2 owner must implement acquire_slot")


def get_pool_stats() -> dict:
    """Returns {resource: in_flight_count} for diagnostics endpoint."""
    # TODO [NFR2]: expose live counters used by the metrics view.
    raise NotImplementedError("NFR2 owner must implement get_pool_stats")
