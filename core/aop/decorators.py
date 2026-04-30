"""
Decorators for performance measurement and call auditing - [AOP].

Idea: wrap the target callable with an instrumentation layer without
touching its business logic.
"""
import functools
import logging
import time
from collections import Counter
from typing import Any, Callable

logger = logging.getLogger("core.aop")

# Per-process counter consumed by the @count_calls decorator. For a truly
# distributed view across web1 and web2 this should be promoted to Redis -
# tracked under NFR10.
_call_counter: Counter = Counter()


def timed(label: str | None = None) -> Callable:
    """Time the wrapped callable and emit a structured log line.

    Example:
        @timed("inventory.decrement_stock")
        def decrement_stock(...): ...
    """
    def decorator(fn: Callable) -> Callable:
        name = label or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.info("timed", extra={"label": name, "ms": round(elapsed_ms, 2)})
        return wrapper
    return decorator


def audit_log(action: str) -> Callable:
    """Log every invocation (start / ok / fail). Useful around critical
    concurrency hot-spots so the request trail can be replayed in incidents.
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            logger.info("audit.start", extra={"action": action})
            try:
                result = fn(*args, **kwargs)
                logger.info("audit.ok", extra={"action": action})
                return result
            except Exception as exc:
                logger.exception("audit.fail", extra={"action": action, "err": str(exc)})
                raise
        return wrapper
    return decorator


def count_calls(label: str) -> Callable:
    """Increment a counter on every call - feeds NFR10 hot-path discovery."""
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            _call_counter[label] += 1
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def get_call_counts() -> dict[str, int]:
    """Snapshot of call counts. Exposed by a diagnostics endpoint."""
    return dict(_call_counter)
