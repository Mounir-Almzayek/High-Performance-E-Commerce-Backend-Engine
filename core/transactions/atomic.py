"""
ACID helpers - [NFR8].

Composite operations (charge payment + decrement stock + create order) MUST
either commit fully or roll back fully. Django's `transaction.atomic` is the
right tool, but we wrap it here so we can enforce two extra invariants:

  1. The transaction must run at the right isolation level.
  2. Side effects that escape the database (Celery tasks, cache writes,
     emails) must be deferred until AFTER commit, otherwise a rollback
     leaves the world in an inconsistent state.

Public surface (filled in by NFR8 owner):

  - atomic_with_isolation(level="read committed")
        Context manager that opens a transaction at a chosen isolation
        level via `connection.cursor().execute("SET TRANSACTION ...")`.

  - on_commit(callback)
        Thin wrapper over django.db.transaction.on_commit that also
        accepts kwargs and logs the deferral.

  - run_saga(steps)
        Linear saga runner for cross-service consistency where 2PC is not
        an option (e.g. external payment gateway). Each step has a
        compensating action; on failure the runner walks back the
        completed steps in reverse.
"""
from contextlib import contextmanager
from typing import Callable, Iterator


@contextmanager
def atomic_with_isolation(level: str = "read committed") -> Iterator[None]:
    """Open a transaction at an explicit isolation level."""
    # TODO [NFR8]: implement using django.db.transaction.atomic +
    #              `SET TRANSACTION ISOLATION LEVEL ...` on the connection.
    raise NotImplementedError("NFR8 owner must implement atomic_with_isolation")
    yield  # pragma: no cover


def on_commit(callback: Callable, **kwargs) -> None:
    """Defer a side effect until the surrounding transaction commits."""
    # TODO [NFR8]: wrap django.db.transaction.on_commit and add structured
    #              logging so deferred callbacks are visible in NFR10 traces.
    raise NotImplementedError("NFR8 owner must implement on_commit")


class SagaStep:
    def __init__(self, action: Callable, compensation: Callable) -> None:
        self.action = action
        self.compensation = compensation


def run_saga(steps: list[SagaStep]) -> None:
    """Sequentially run actions; on failure, walk back compensations."""
    # TODO [NFR8]: implement and document idempotency requirements for
    #              compensations.
    raise NotImplementedError("NFR8 owner must implement run_saga")
