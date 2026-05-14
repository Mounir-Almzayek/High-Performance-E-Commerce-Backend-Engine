from __future__ import annotations

import threading
import time

import pytest
from django.test import override_settings
from rest_framework import status

from core.resources.pool import (
    CapacityExceeded,
    acquire_slot,
    bounded_executor,
    get_pool_stats,
    release_slot,
    resource_slot,
)


def _settings_for(resource: str, limit: int):
    return override_settings(
        INTERNAL_POOL_MAX_CONCURRENCY=max(limit, 1),
        RESOURCE_ACQUIRE_TIMEOUT_SECONDS=0.01,
        RESOURCE_LIMITS={resource: limit},
    )


def test_acquire_slot_rejects_when_resource_is_full_from_another_thread():
    resource = "unit_full"
    with _settings_for(resource, 1):
        assert acquire_slot(resource, timeout=0)

        result: list[bool] = []

        def attempt_acquire():
            result.append(acquire_slot(resource, timeout=0))

        thread = threading.Thread(target=attempt_acquire)
        thread.start()
        thread.join()

        assert result == [False]
        stats = get_pool_stats()[resource]
        assert stats["limit"] == 1
        assert stats["in_flight"] == 1
        assert stats["available"] == 0
        assert stats["acquired_total"] == 1
        assert stats["rejected_total"] == 1

        release_slot(resource)


def test_resource_slot_releases_capacity_when_wrapped_code_raises():
    resource = "unit_exception"
    with _settings_for(resource, 1):
        with pytest.raises(RuntimeError):
            with resource_slot(resource):
                raise RuntimeError("boom")

        stats = get_pool_stats()[resource]
        assert stats["in_flight"] == 0
        assert stats["available"] == 1


def test_resource_slot_raises_capacity_exceeded_with_503_metadata():
    resource = "unit_capacity_exception"
    with _settings_for(resource, 1):
        assert acquire_slot(resource, timeout=0)

        caught: list[CapacityExceeded] = []

        def attempt_context():
            try:
                with resource_slot(resource, timeout=0):
                    pass
            except CapacityExceeded as exc:
                caught.append(exc)

        thread = threading.Thread(target=attempt_context)
        thread.start()
        thread.join()

        assert len(caught) == 1
        assert caught[0].status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert caught[0].wait == 1

        release_slot(resource)


def test_release_slot_is_safe_when_called_too_many_times():
    resource = "unit_over_release"
    with _settings_for(resource, 1):
        assert acquire_slot(resource, timeout=0)
        release_slot(resource)
        release_slot(resource)

        stats = get_pool_stats()[resource]
        assert stats["in_flight"] == 0
        assert stats["available"] == 1


def test_same_thread_acquisition_is_reentrant_without_consuming_extra_capacity():
    resource = "unit_reentrant"
    with _settings_for(resource, 1):
        with resource_slot(resource):
            with resource_slot(resource):
                stats = get_pool_stats()[resource]
                assert stats["in_flight"] == 1
                assert stats["available"] == 0

        assert get_pool_stats()[resource]["in_flight"] == 0


def test_bounded_executor_caps_parallel_work_to_resource_limit():
    resource = "unit_executor"
    max_seen = 0
    running = 0
    lock = threading.Lock()

    def work():
        nonlocal max_seen, running
        with lock:
            running += 1
            max_seen = max(max_seen, running)
        time.sleep(0.03)
        with lock:
            running -= 1
        return 1

    with _settings_for(resource, 2):
        with bounded_executor(max_workers=6, resource=resource) as executor:
            futures = [executor.submit(work) for _ in range(8)]
            assert sum(f.result(timeout=1) for f in futures) == 8

        assert max_seen <= 2
        stats = get_pool_stats()[resource]
        assert stats["in_flight"] == 0
        assert stats["available"] == 2
        assert stats["acquired_total"] == 8
