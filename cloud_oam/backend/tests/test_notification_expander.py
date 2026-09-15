from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app import notification_expander as worker
from app.formal_services.notification_expansion import NotificationExpansionError


class _Session:
    def __init__(self):
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_once_commits_bounded_expansion_and_emits_ids(monkeypatch, capsys):
    session = _Session()
    event_id = uuid4()
    monkeypatch.setattr(worker, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        worker,
        "expand_pending_notification_events",
        lambda db, limit: (
            SimpleNamespace(event_id=event_id, created_delivery_count=2),
        ),
    )

    assert worker.main(["--once", "--limit", "7"]) == 0

    assert session.committed is True
    assert session.rolled_back is False
    assert session.closed is True
    output = capsys.readouterr().out
    assert '"createdDeliveries":2' in output
    assert str(event_id) in output


def test_once_rolls_back_on_expansion_error(monkeypatch, capsys):
    session = _Session()
    monkeypatch.setattr(worker, "SessionLocal", lambda: session)

    def fail(_db, *, limit):
        raise NotificationExpansionError("limit invalid")

    monkeypatch.setattr(worker, "expand_pending_notification_events", fail)

    assert worker.main(["--once"]) == 2

    assert session.committed is False
    assert session.rolled_back is True
    assert session.closed is True
    assert "notification_expansion_invalid" in capsys.readouterr().out


def test_invalid_worker_arguments_fail_before_opening_database(monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(worker, "SessionLocal", lambda: opened.append(True))

    assert worker.main(["--once", "--limit", "0"]) == 2
    assert opened == []
    assert "notification_expansion_limit_invalid" in capsys.readouterr().out

