"""
ACID helpers - [NFR8].

Composite operations (charge payment + decrement stock + create order) MUST
either commit fully or roll back fully, even under concurrent access. Django's
`transaction.atomic` is the right tool; we wrap it here so we can enforce two
extra invariants that bare `atomic()` does not make visible:

  1. The transaction runs at an EXPLICIT, code-reviewable isolation level.
  2. Side effects that escape the database (Celery tasks, cache writes,
     emails) are deferred until AFTER commit, so a rollback can never leave
     the outside world believing in state the database threw away.

Public surface:

  - atomic_with_isolation(level="read committed")
        Context manager that opens a transaction at a chosen isolation level.
        On PostgreSQL it issues `SET TRANSACTION ISOLATION LEVEL ...` so the
        choice is explicit in code review and visible in the SQL log. Nested
        use becomes a SAVEPOINT and inherits the outer level (PostgreSQL
        forbids changing the level mid-transaction).

  - on_commit(callback, **kwargs)
        Thin wrapper over django.db.transaction.on_commit that binds kwargs
        and logs both the scheduling and the execution of the deferred
        callback, so async dispatch / cache busts are traceable in NFR10.

  - run_saga(steps)
        Linear saga runner for cross-service consistency where a single DB
        transaction (and therefore ROLLBACK) is NOT available, e.g. an
        external payment gateway. Runs each action in order; on failure it
        walks back the COMPLETED steps in reverse, calling each
        compensation. Compensations MUST be idempotent (a compensation may
        be retried, and must produce the same effect as running it once).

        NOTE: the current project settles payment against an in-DB simulated
        wallet, so `atomic_with_isolation` already gives true all-or-nothing
        and a saga is NOT required for the existing flow. `run_saga` is kept
        as the correct, tested pattern for the day a real external gateway is
        introduced.
"""
from __future__ import annotations

import functools
import logging
from contextlib import contextmanager
from typing import Callable, Iterator

from django.db import connection, transaction

logger = logging.getLogger("core.transactions")

# Whitelisted so the level can be interpolated into SQL safely (the value
# cannot be parameterized in `SET TRANSACTION ISOLATION LEVEL`).
_ISOLATION_LEVELS = {
    "read committed": "READ COMMITTED",
    "repeatable read": "REPEATABLE READ",
    "serializable": "SERIALIZABLE",
}


@contextmanager
def atomic_with_isolation(level: str = "read committed") -> Iterator[None]:
    """Open a transaction at an explicit isolation level.

    Args:
        level: one of "read committed", "repeatable read", "serializable"
            (case-insensitive). Default matches PostgreSQL's own default.

    Raises:
        ValueError: if `level` is not a recognized isolation level.
    """
    key = level.strip().lower()
    if key not in _ISOLATION_LEVELS:
        raise ValueError(
            f"unknown isolation level {level!r}; "
            f"choose one of {sorted(_ISOLATION_LEVELS)}"
        )
    sql_level = _ISOLATION_LEVELS[key]

    # `SET TRANSACTION ISOLATION LEVEL` is only legal as the first statement
    # of a transaction, and only on the OUTERMOST block. A nested call would
    # open a SAVEPOINT and inherit the surrounding level, so we must not (and
    # cannot) re-issue the SET there.
    is_outermost = not connection.in_atomic_block

    with transaction.atomic():
        if is_outermost and connection.vendor == "postgresql":
            with connection.cursor() as cur:
                cur.execute(f"SET TRANSACTION ISOLATION LEVEL {sql_level}")
            logger.debug("tx.isolation.set", extra={"isolation": sql_level})
        elif not is_outermost:
            logger.debug(
                "tx.isolation.nested_inherits_outer",
                extra={"requested": sql_level},
            )
        yield


def on_commit(callback: Callable, **kwargs) -> None:
    """Defer a side effect until the surrounding transaction commits.

    Binds any kwargs to `callback` and logs both the scheduling and the
    eventual execution, so deferred work (Celery dispatch, cache busting) is
    visible in NFR10 traces. If there is no active transaction, Django runs
    the callback immediately — the same semantics as `transaction.on_commit`.
    """
    bound = functools.partial(callback, **kwargs) if kwargs else callback
    label = getattr(callback, "__name__", repr(callback))

    def _run_logged() -> None:
        logger.info("tx.on_commit.run", extra={"callback": label})
        bound()

    logger.debug("tx.on_commit.scheduled", extra={"callback": label})
    transaction.on_commit(_run_logged)


class SagaStep:
    def __init__(self, action: Callable, compensation: Callable) -> None:
        self.action = action
        self.compensation = compensation


def run_saga(steps: list[SagaStep]) -> None:
    """Sequentially run actions; on failure, walk back compensations.

    Each `action` is run in order. If one raises, every PREVIOUSLY completed
    step is compensated in reverse order, then the original exception is
    re-raised. The failing step's own compensation is NOT run (its action did
    not complete). Compensations must be idempotent and are best-effort: a
    failing compensation is logged but does not stop the rollback of the
    remaining completed steps.
    """
    completed: list[SagaStep] = []
    try:
        for step in steps:
            step.action()
            completed.append(step)
    except Exception:
        logger.warning(
            "saga.failed_compensating",
            extra={"completed": len(completed), "total": len(steps)},
        )
        for step in reversed(completed):
            try:
                step.compensation()
            except Exception:  # pragma: no cover - compensation is best-effort
                logger.exception("saga.compensation_failed")
        raise
