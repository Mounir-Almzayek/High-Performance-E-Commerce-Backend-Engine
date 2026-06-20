import os
import threading
import time

from django.conf import settings
from django_redis import get_redis_connection
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from core.resources.pool import get_pool_stats


_PROCESS_STARTED_AT = time.time()


def _read_proc_status() -> dict[str, int | None]:
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


class CacheDiagnosticsView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        try:
            conn = get_redis_connection("default")
            info = conn.info()

            hits = info.get("keyspace_hits", 0)
            misses = info.get("keyspace_misses", 0)
            total = hits + misses
            hit_ratio = round(hits / total, 4) if total > 0 else 0.0

            def count_keys(pattern: str) -> int:
                cursor, keys = conn.scan(cursor=0, match=pattern, count=500)
                total_keys = len(keys)
                while cursor:
                    cursor, batch = conn.scan(cursor=cursor, match=pattern, count=500)
                    total_keys += len(batch)
                return total_keys

            return Response({
                "instance_id": settings.INSTANCE_ID,
                "redis": {
                    "used_memory_human": info.get("used_memory_human"),
                    "connected_clients": info.get("connected_clients"),
                    "keyspace_hits": hits,
                    "keyspace_misses": misses,
                    "hit_ratio": hit_ratio,
                },
                "key_counts": {
                    "product_detail": count_keys("product:[0-9]*"),
                    "product_list": count_keys("product:list:*"),
                    "cart": count_keys("cart:*"),
                    "inventory_level": count_keys("inventory:level:*"),
                    "rebuild_locks": count_keys("sflock:*"),
                },
            })
        except Exception as exc:  # noqa: BLE001
            return Response({"error": str(exc)}, status=503)
