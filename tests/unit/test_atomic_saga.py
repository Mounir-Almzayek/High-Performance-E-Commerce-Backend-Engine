"""
Unit tests for the saga runner (NFR8).

Lecture mapping:
  - "Compensating transactions / SAGA pattern" -> when a workflow spans
    systems that CANNOT share one database transaction (e.g. an external
    payment gateway), `ROLLBACK` is not available. A saga instead runs the
    forward actions one by one and, on failure, walks back the COMPLETED
    steps in reverse order by invoking each step's compensation.

Design note (defensible in review): the project currently settles payment
against an in-DB *simulated wallet*, so `@transaction.atomic` already gives
true all-or-nothing and a saga is NOT required for the existing flow. These
tests pin the runner's contract so the pattern is correct and ready the day
a real external gateway (whose effects escape our DB) is introduced.
"""
from __future__ import annotations

import pytest

from core.transactions.atomic import SagaStep, run_saga


def test_all_actions_run_in_order_and_nothing_is_compensated():
    log: list[str] = []
    steps = [
        SagaStep(lambda: log.append("do-1"), lambda: log.append("undo-1")),
        SagaStep(lambda: log.append("do-2"), lambda: log.append("undo-2")),
        SagaStep(lambda: log.append("do-3"), lambda: log.append("undo-3")),
    ]

    run_saga(steps)

    assert log == ["do-1", "do-2", "do-3"]


def test_failure_compensates_only_completed_steps_in_reverse():
    log: list[str] = []

    def third_action_fails():
        log.append("do-3-fails")
        raise RuntimeError("step 3 blew up")

    steps = [
        SagaStep(lambda: log.append("do-1"), lambda: log.append("undo-1")),
        SagaStep(lambda: log.append("do-2"), lambda: log.append("undo-2")),
        SagaStep(third_action_fails, lambda: log.append("undo-3")),
        SagaStep(lambda: log.append("do-4"), lambda: log.append("undo-4")),
    ]

    with pytest.raises(RuntimeError, match="step 3 blew up"):
        run_saga(steps)

    # - step 4 never starts (we stop at the first failure)
    # - step 3's OWN compensation does NOT run: its action did not complete
    # - only the two fully-completed steps are compensated, newest-first
    assert log == ["do-1", "do-2", "do-3-fails", "undo-2", "undo-1"]
