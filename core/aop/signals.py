"""
Audit signals - [AOP].

Persistence-level hooks intended for high-value entities (Order, Payment,
StockMovement). Actual receivers are registered in each app's apps.py.
"""
import logging

logger = logging.getLogger("core.aop")


def log_save(sender, instance, created, **kwargs) -> None:
    """Generic post_save receiver. Apps wire this up via signals.connect()."""
    logger.info(
        "model.saved",
        extra={
            "model": sender.__name__,
            "pk": getattr(instance, "pk", None),
            "created": created,
        },
    )
