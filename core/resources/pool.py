"""
Resource governance - [NFR2].

This module owns the inner concurrency cap for work performed inside a
single Django/Celery process. The outer cap still belongs to Gunicorn and
Celery worker settings; this layer prevents fan-out work and hot service
paths from consuming all process-local capacity at once.
"""
from __future__ import annotations

import functools
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, ParamSpec, TypeVar

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rest_framework.exceptions import APIException

logger = logging.getLogger("core.resources")

P = ParamSpec("P")
R = TypeVar("R")


class CapacityExceeded(APIException):
    """Raised when a named resource pool cannot admit more work quickly."""

    status_code = 503
    default_code = "capacity_exceeded"
    wait = 1

    def __init__(self, resource: str, timeout: float) -> None:
        self.resource = resource
        self.timeout = timeout
        super().__init__(
            detail={
                "detail": "Resource capacity exceeded. Retry shortly.",
                "resource": resource,
            }
        )


@dataclass
class _ResourcePool:
    name: str
    limit: int
    semaphore: threading.BoundedSemaphore = field(init=False)
    lock: threading.Lock = field(default_factory=threading.Lock)
    in_flight: int = 0
    acquired_total: int = 0
    rejected_total: int = 0

    def __post_init__(self) -> None:
        self.semaphore = threading.BoundedSemaphore(self.limit)

    def acquire(self, timeout: float) -> bool:
        if timeout <= 0:
            acquired = self.semaphore.acquire(blocking=False)
        else:
            acquired = self.semaphore.acquire(timeout=timeout)

        with self.lock:
            if acquired:
                self.in_flight += 1
                self.acquired_total += 1
            else:
                self.rejected_total += 1
        return acquired

    def release(self) -> None:
        with self.lock:
            if self.in_flight <= 0:
                logger.warning(
                    "resource_pool.release_without_acquire",
                    extra={"resource": self.name},
                )
                return
            self.in_flight -= 1
        self.semaphore.release()

    def snapshot(self) -> dict[str, int]:
        with self.lock:
            in_flight = self.in_flight
            return {
                "limit": self.limit,
                "in_flight": in_flight,
                "available": max(self.limit - in_flight, 0),
                "acquired_total": self.acquired_total,
                "rejected_total": self.rejected_total,
            }


_pools: dict[str, _ResourcePool] = {}
_pools_lock = threading.Lock()
_thread_state = threading.local()


def _held_counts() -> dict[str, int]:
    counts = getattr(_thread_state, "held_counts", None)
    if counts is None:
        counts = {}
        _thread_state.held_counts = counts
    return counts


def _configured_limits() -> dict[str, int]:
    configured = dict(getattr(settings, "RESOURCE_LIMITS", {}))
    configured.setdefault("internal_pool", settings.INTERNAL_POOL_MAX_CONCURRENCY)
    return configured


def _limit_for(resource: str) -> int:
    limits = _configured_limits()
    limit = int(limits.get(resource, settings.INTERNAL_POOL_MAX_CONCURRENCY))
    if limit <= 0:
        raise ImproperlyConfigured(
            f"Resource limit for {resource!r} must be a positive integer."
        )
    return limit


def _default_timeout(timeout: float | None) -> float:
    if timeout is not None:
        return max(float(timeout), 0.0)
    return max(float(getattr(settings, "RESOURCE_ACQUIRE_TIMEOUT_SECONDS", 1.0)), 0.0)


def _get_pool(resource: str) -> _ResourcePool:
    limit = _limit_for(resource)
    with _pools_lock:
        pool = _pools.get(resource)
        if pool is None or (pool.limit != limit and pool.in_flight == 0):
            pool = _ResourcePool(resource, limit)
            _pools[resource] = pool
        return pool


def acquire_slot(resource: str, timeout: float | None = None) -> bool:
    """Try to admit work into a named capacity pool.

    The same thread may re-enter the same resource without consuming a
    second physical slot. This keeps composite flows such as webhooks
    from deadlocking when they call lower-level payment services.
    """
    held = _held_counts()
    if held.get(resource, 0) > 0:
        held[resource] += 1
        return True

    timeout_seconds = _default_timeout(timeout)
    pool = _get_pool(resource)
    acquired = pool.acquire(timeout_seconds)
    if acquired:
        held[resource] = 1
        logger.debug(
            "resource_pool.acquire",
            extra={"resource": resource, "in_flight": pool.snapshot()["in_flight"]},
        )
    else:
        logger.warning(
            "resource_pool.reject",
            extra={"resource": resource, "timeout": timeout_seconds},
        )
    return acquired


def release_slot(resource: str) -> None:
    """Release a slot acquired by acquire_slot.

    Extra releases are ignored and logged instead of corrupting the
    semaphore, which keeps cleanup paths defensive.
    """
    held = _held_counts()
    count = held.get(resource, 0)
    if count > 1:
        held[resource] = count - 1
        return
    if count <= 0:
        logger.warning(
            "resource_pool.release_without_thread_owner",
            extra={"resource": resource},
        )
        return

    held.pop(resource, None)
    _get_pool(resource).release()


@contextmanager
def resource_slot(resource: str, timeout: float | None = None) -> Iterator[None]:
    """Context manager that raises CapacityExceeded when no slot is available."""
    timeout_seconds = _default_timeout(timeout)
    if not acquire_slot(resource, timeout=timeout_seconds):
        raise CapacityExceeded(resource=resource, timeout=timeout_seconds)
    try:
        yield
    finally:
        release_slot(resource)


def capacity_limited(
    resource: str,
    timeout: float | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator form of resource_slot for service entrypoints."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with resource_slot(resource, timeout=timeout):
                return func(*args, **kwargs)

        return wrapper

    return decorator


class CapacityLimitedThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor that instruments every submitted task."""

    def __init__(
        self,
        *,
        resource: str,
        acquire_timeout: float | None,
        max_workers: int,
        thread_name_prefix: str,
    ) -> None:
        self.resource = resource
        self.acquire_timeout = acquire_timeout
        super().__init__(max_workers=max_workers, thread_name_prefix=thread_name_prefix)

    def submit(self, fn: Callable[..., R], /, *args: Any, **kwargs: Any) -> Future[R]:
        return super().submit(self._run_with_slot, fn, args, kwargs)

    def _run_with_slot(
        self,
        fn: Callable[..., R],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> R:
        with resource_slot(self.resource, timeout=self.acquire_timeout):
            return fn(*args, **kwargs)


@contextmanager
def bounded_executor(
    max_workers: int | None = None,
    *,
    resource: str = "internal_pool",
    thread_name_prefix: str | None = None,
    acquire_timeout: float | None = None,
) -> Iterator[CapacityLimitedThreadPoolExecutor]:
    """Yield a capacity-aware ThreadPoolExecutor.

    The worker count is capped to the configured resource limit. Submitted
    tasks also acquire a slot so multiple executors in the same process
    cannot collectively exceed the named resource capacity.
    """
    limit = _limit_for(resource)
    requested = max_workers if max_workers is not None else limit
    cap = max(1, min(int(requested), limit))
    prefix = thread_name_prefix or f"{resource}_worker"

    executor = CapacityLimitedThreadPoolExecutor(
        resource=resource,
        acquire_timeout=acquire_timeout,
        max_workers=cap,
        thread_name_prefix=prefix,
    )

    logger.debug(
        "bounded_executor.started",
        extra={"resource": resource, "max_workers": cap, "limit": limit},
    )
    try:
        yield executor
    finally:
        executor.shutdown(wait=True)
        logger.debug("bounded_executor.shutdown", extra={"resource": resource})


def get_pool_stats() -> dict[str, dict[str, int]]:
    """Return live capacity stats for configured and observed resources."""
    resources = set(_configured_limits())
    with _pools_lock:
        resources.update(_pools)

    stats: dict[str, dict[str, int]] = {}
    for resource in sorted(resources):
        pool = _get_pool(resource)
        stats[resource] = pool.snapshot()
    return stats
