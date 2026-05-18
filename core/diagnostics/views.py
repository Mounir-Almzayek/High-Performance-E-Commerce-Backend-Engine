import os
import threading
import time

from django.conf import settings
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from core.resources.pool import get_pool_stats


_PROCESS_STARTED_AT = time.time()


def _read_proc_status() -> dict[str, int | None]:
    """Return Linux /proc process metrics when available.

    Docker demo containers run on Linux, so /proc gives us CPU/RAM/thread
    evidence without adding another runtime dependency. Local Windows runs
    simply return None for Linux-only fields.
    """
    metrics: dict[str, int | None] = {
        "rss_kb": None,
        "peak_rss_kb": None,
        "thread_count": None,
    }
    try:
        with open("/proc/self/status", encoding="utf-8") as status_file:
            for line in status_file:
                key, _, value = line.partition(":")
                value = value.strip()
                if key == "VmRSS":
                    metrics["rss_kb"] = int(value.split()[0])
                elif key == "VmHWM":
                    metrics["peak_rss_kb"] = int(value.split()[0])
                elif key == "Threads":
                    metrics["thread_count"] = int(value)
    except (OSError, ValueError):
        pass
    return metrics


def _load_average() -> list[float] | None:
    try:
        return [round(value, 2) for value in os.getloadavg()]
    except (AttributeError, OSError):
        return None


class PoolDiagnosticsView(APIView):
    """Expose live process-local capacity counters for NFR2."""

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response(
            {
                "instance_id": settings.INSTANCE_ID,
                "outer_caps": {
                    "gunicorn_workers": settings.GUNICORN_WORKERS,
                    "gunicorn_threads": settings.GUNICORN_THREADS,
                    "gunicorn_worker_class": settings.GUNICORN_WORKER_CLASS,
                    "gunicorn_timeout": settings.GUNICORN_TIMEOUT,
                    "celery_concurrency": settings.CELERY_CONCURRENCY,
                },
                "resource_acquire_timeout_seconds": settings.RESOURCE_ACQUIRE_TIMEOUT_SECONDS,
                "pools": get_pool_stats(),
            }
        )


class ProcessDiagnosticsView(APIView):
    """Expose process-level CPU/RAM/thread counters for NFR2 monitoring."""

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        proc_status = _read_proc_status()
        cpu_time_seconds = time.process_time()
        uptime_seconds = max(time.time() - _PROCESS_STARTED_AT, 0.001)

        return Response(
            {
                "instance_id": settings.INSTANCE_ID,
                "pid": os.getpid(),
                "uptime_seconds": round(uptime_seconds, 3),
                "cpu": {
                    "process_cpu_seconds": round(cpu_time_seconds, 3),
                    "process_cpu_per_uptime_percent": round(
                        (cpu_time_seconds / uptime_seconds) * 100,
                        2,
                    ),
                    "system_load_average": _load_average(),
                },
                "memory": {
                    "rss_kb": proc_status["rss_kb"],
                    "peak_rss_kb": proc_status["peak_rss_kb"],
                },
                "threads": {
                    "python_active_count": threading.active_count(),
                    "process_thread_count": proc_status["thread_count"],
                },
            }
        )


class InstanceView(APIView):
    """
    GET /api/v1/instance/
    Lightweight endpoint that identifies which backend served this request.
    Used by NFR5 distribution scripts and the load_distribution_sim metrics
    poller to confirm Nginx is routing across all instances.

    Note: X-Instance-Id is also added to every response by PerformanceMiddleware,
    so this endpoint is an explicit human-readable alternative.
    """

    permission_classes = [permissions.AllowAny]

    def get(self, request):
        return Response(
            {
                "instance_id": settings.INSTANCE_ID,
                "note": (
                    "This counter is per-process (RAM only). "
                    "Each instance reports independently. "
                    "For cross-instance totals, sum all instances or migrate to Redis (NFR10)."
                ),
            }
        )
