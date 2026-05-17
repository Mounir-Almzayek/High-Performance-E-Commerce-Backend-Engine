from django.conf import settings
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from core.resources.pool import get_pool_stats


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
