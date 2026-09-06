from __future__ import annotations

from decimal import Decimal

import pytest

from app.formal_services import stocktake_posting_command_seal as seal
from app.formal_services import stocktake_posting_command_status as status
from app.stocktake_models import StocktakePostingCommandOutcome
from test_stocktake_safe_posting_service import _approve, posting_world
from test_stocktake_review_recount_service import review_world
from test_stocktake_difference_service import world
from test_stocktake_task_service import db
from test_stocktake_task_service import NOW
import app.formal_services.stocktake_task as task_service
import app.formal_services.stocktake_count as count_service
import app.formal_services.stocktake_difference as difference_service
import app.formal_services.stocktake_review as review_service
import app.formal_services.stocktake_recount as recount_service
from test_stocktake_review_recount_service import EVALUATED_AT, REGION_REVIEWED_AT, RECOUNT_OPENED_AT


@pytest.fixture(autouse=True)
def fixed_clocks(monkeypatch):
    monkeypatch.setattr(task_service, "_database_now", lambda _db: NOW)
    monkeypatch.setattr(count_service, "_database_now", lambda _db: NOW)
    monkeypatch.setattr(difference_service, "_database_now", lambda _db: EVALUATED_AT)
    monkeypatch.setattr(review_service, "_database_now", lambda _db: REGION_REVIEWED_AT)
    monkeypatch.setattr(recount_service, "_database_now", lambda _db: RECOUNT_OPENED_AT)


def _seal(world, task, trace):
    actor = world.principals["admin"]
    return seal.seal_nonopening_stocktake_post_command(
        world.db,
        actor=actor,
        task_id=task.id,
        expected_task_version=task.version,
        actor_person_id=actor.person_id,
        actor_authorization_version=actor.authorization_version,
        trace_request_id=trace,
    )


def test_seal_is_append_only_and_does_not_advance_task_or_inventory(posting_world, monkeypatch):
    task, _ = _approve(posting_world, monkeypatch, key="seal-only", counted_qty=Decimal("4.000"), decision="no_adjustment")
    before = (task.status, task.version, len(posting_world.db.query(StocktakePostingCommandOutcome).all()))
    result = _seal(posting_world, task, "trace-seal-only")
    posting_world.db.commit()
    posting_world.db.refresh(task)
    assert result.task_id == task.id
    assert result.trace_request_id == "trace-seal-only"
    assert (task.status, task.version) == before[:2]
    rows = posting_world.db.query(StocktakePostingCommandOutcome).filter_by(task_id=task.id).all()
    assert len(rows) == 1 and rows[0].disposition == "sealed_not_executed"
    found = status.stocktake_posting_command_status(
        posting_world.db, actor=posting_world.principals["admin"], task_id=task.id,
        actor_person_id=posting_world.principals["admin"].person_id,
        actor_authorization_version=posting_world.principals["admin"].authorization_version,
        trace_request_id="trace-seal-only",
    )
    assert found.lookup_status == "sealed_not_executed"


def test_same_seal_replays_but_different_version_conflicts(posting_world, monkeypatch):
    task, _ = _approve(posting_world, monkeypatch, key="seal-replay", counted_qty=Decimal("4.000"), decision="no_adjustment")
    first = _seal(posting_world, task, "trace-seal-replay")
    second = _seal(posting_world, task, "trace-seal-replay")
    assert second.seal_id == first.seal_id
    with pytest.raises(seal.StocktakePostingSealError) as caught:
        seal.seal_nonopening_stocktake_post_command(
            posting_world.db, actor=posting_world.principals["admin"], task_id=task.id,
            expected_task_version=task.version + 1,
            actor_person_id=posting_world.principals["admin"].person_id,
            actor_authorization_version=posting_world.principals["admin"].authorization_version,
            trace_request_id="trace-seal-replay",
        )
    assert caught.value.category == "precondition_failed"
