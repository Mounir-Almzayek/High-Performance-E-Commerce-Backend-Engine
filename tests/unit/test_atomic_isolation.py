"""
Unit tests for `atomic_with_isolation` and the logged `on_commit` wrapper
(NFR8). These are the two primitives the composite operations are refactored
onto, so the isolation choice becomes explicit and every deferred side effect
is logged.
"""
from __future__ import annotations

import pytest
from django.db import connection, transaction

from core.transactions.atomic import atomic_with_isolation, on_commit


# --------------------------- atomic_with_isolation -------------------------


@pytest.mark.django_db(transaction=True)
def test_body_runs_inside_a_transaction():
    assert connection.in_atomic_block is False
    with atomic_with_isolation("read committed"):
        assert connection.in_atomic_block is True


@pytest.mark.django_db(transaction=True)
def test_sets_requested_isolation_level_on_postgres():
    if connection.vendor != "postgresql":
        pytest.skip("isolation level is only enforced on PostgreSQL")
    with atomic_with_isolation("serializable"):
        with connection.cursor() as cur:
            cur.execute("SHOW transaction_isolation")
            level = cur.fetchone()[0]
    assert level == "serializable"


@pytest.mark.django_db(transaction=True)
def test_rejects_unknown_isolation_level():
    with pytest.raises(ValueError, match="isolation level"):
        with atomic_with_isolation("definitely-not-a-level"):
            pass  # pragma: no cover


# --------------------------------- on_commit -------------------------------


def test_on_commit_runs_callback_after_commit(db, django_capture_on_commit_callbacks):
    ran: list[str] = []
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with transaction.atomic():
            on_commit(lambda: ran.append("committed"))
            assert ran == []  # deferred — must NOT run before commit
    assert ran == ["committed"]
    assert len(callbacks) == 1


def test_on_commit_callback_is_dropped_on_rollback(db, django_capture_on_commit_callbacks):
    ran: list[str] = []
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        with pytest.raises(ValueError):
            with transaction.atomic():
                on_commit(lambda: ran.append("should-not-run"))
                raise ValueError("boom")
    assert ran == []
    assert callbacks == []
