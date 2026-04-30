"""
PerformanceMiddleware - [AOP].

Responsibilities:
  1. Time every HTTP request (wall-clock).
  2. Tag the response with X-Instance-Id so NFR5 (load distribution) can
     verify how Nginx spreads requests across web1 / web2.
  3. Emit a structured log line per request consumed by NFR10 reports.

It is intentionally placed early in MIDDLEWARE so the timing window covers
every downstream middleware and view.
"""
import logging
import time
from typing import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse

logger = logging.getLogger("core.aop")


class PerformanceMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self.instance_id = getattr(settings, "INSTANCE_ID", "local")

    def __call__(self, request: HttpRequest) -> HttpResponse:
        start = time.perf_counter()
        response = self.get_response(request)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

        response["X-Instance-Id"] = self.instance_id
        response["X-Response-Time-ms"] = str(elapsed_ms)

        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "ms": elapsed_ms,
                "instance": self.instance_id,
            },
        )
        return response
