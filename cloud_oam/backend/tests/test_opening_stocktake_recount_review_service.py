from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import count
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import func, select

import app.formal_services.opening_stocktake_recount as recount_service
import app.formal_services.opening_observation_disposition as disposition_service
from app.formal_access import load_formal_principal
from app.formal_services.opening_observation_disposition import (
    RecordOpeningObservationDispositionCommand,
    record_opening_observation_disposition,
)
from app.formal_services.opening_stocktake_count import (
    OpeningPhysicalObservationInput,
    SubmitOpeningStocktakeScopeCountCommand,
    submit_opening_stocktake_scope_count,
)
from app.formal_services.opening_stocktake_recount import (
    OpeningStocktakeRecountError,
    open_opening_stocktake_recount,
)
from app.formal_services.opening_stocktake_review import (
    OpeningStocktakeReviewError,
    submit_opening_headquarters_review,
    submit_opening_region_review,
)
from app.foundation_models import AuditEvent, OutboxEvent, RoleAssignment, StateTransitionEvent
from app.stocktake_models import (
    FormalStocktakeTask,
    StocktakeRecountCase,
    StocktakeRecountScopeAssignment,
    StocktakeCountObservation,
    StocktakeObservationDisposition,
    StocktakeReview,
    StocktakeRound,
    StocktakeScopeCountCompletion,
)

from test_opening_stocktake_recount_count_service import (  # noqa: E402
    NOW,
    _fixed_database_times,
    _fixed_recount_count_time,
    _fixed_recount_time,
    _open_round_two,
    _submit_round_two,
    db,
    world,
)
from test_opening_stocktake_recount_service import (  # noqa: E402
    _command as _open_recount_command,
    _prepare_recount_required,
)
from test_opening_stocktake_review_service import _review_command  # noqa: E402


@pytest.fixture(autouse=True)
def _round_n_recount_clock(
    monkeypatch: pytest.MonkeyPatch,
    _fixed_database_times: None,
    _fixed_recount_time: None,
    _fixed_recount_count_time: None,
) -> None:
    ticks = count()

    def _clock(db):
        current_round_no = db.scalar(
            select(func.max(FormalStocktakeTask.current_round_no))
        ) or 1
        base_hours = 3 if current_round_no == 1 else 6 + current_round_no
        return NOW + timedelta(
            hours=base_hours,
            microseconds=next(ticks),
        )

    monkeypatch.setattr(recount_service, "_database_now", _clock)


def _submitted_round_two(world) -> SimpleNamespace:
    prepared = _open_round_two(world)
    result = _submit_round_two(
        world,
        prepared,
        actor_name="manager_x",
        key=f"opening-round-two-count-{uuid.uuid4().hex}",
    )
    assert result.round_sealed is True
    world.db.flush()
    prepared.task = world.db.get(FormalStocktakeTask, prepared.task.id)
    prepared.round = world.db.get(StocktakeRound, prepared.round.id)
    assert prepared.task is not None and prepared.task.status == "submitted"
    assert prepared.round is not None and prepared.round.status == "submitted"
    return prepared


def _open_round_three(world, prepared: SimpleNamespace, *, key: str):
    return open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=_open_recount_command(
            prepared.task,
            prepared.round,
            prepared.scope,
            world.manager_x.user.id,
        ),
        idempotency_key=key,
        request_id=f"{key}-request",
    )


def _first_recount_with_submitted_round_two(world, *, key: str) -> SimpleNamespace:
    task, source_round, scope = _prepare_recount_required(world)
    command = _open_recount_command(
        task,
        source_round,
        scope,
        world.manager_x.user.id,
    )
    opened = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=key,
        request_id=f"{key}-open-request",
    )
    prepared = SimpleNamespace(
        task=world.db.get(FormalStocktakeTask, task.id),
        source_round=source_round,
        round=world.db.get(StocktakeRound, opened.next_round_id),
        scope=scope,
        recount_case_id=opened.recount_case_id,
    )
    _submit_round_two(
        world,
        prepared,
        actor_name="manager_x",
        key=f"{key}-round-two-count",
    )
    world.db.flush()
    prepared.task = world.db.get(FormalStocktakeTask, task.id)
    prepared.round = world.db.get(StocktakeRound, opened.next_round_id)
    assert prepared.task is not None and prepared.task.status == "submitted"
    assert prepared.round is not None and prepared.round.status == "submitted"
    prepared.command = command
    prepared.key = key
    return prepared


def test_round_two_region_and_headquarters_approve(world) -> None:
    prepared = _submitted_round_two(world)
    region = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(world.db, prepared, decision="approve"),
        idempotency_key="opening-round-two-region-approve",
        request_id="opening-round-two-region-approve-request",
    )
    headquarters = submit_opening_headquarters_review(
        world.db,
        actor=world.principals["admin"],
        command=_review_command(world.db, prepared, decision="approve"),
        idempotency_key="opening-round-two-headquarters-approve",
        request_id="opening-round-two-headquarters-approve-request",
    )

    assert region.resulting_task_status == "hq_review"
    assert headquarters.resulting_task_status == "approved"
    task = world.db.get(FormalStocktakeTask, prepared.task.id)
    assert task is not None and task.status == "approved"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeReview).where(
            StocktakeReview.round_id == prepared.round.id
        )
    ) == 2


def test_round_two_pending_observation_can_be_disposed_then_legally_recounted(
    world,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _open_round_two(world)
    result = submit_opening_stocktake_scope_count(
        world.db,
        actor=world.principals["manager_x"],
        command=SubmitOpeningStocktakeScopeCountCommand(
            task_id=prepared.task.id,
            round_id=prepared.round.id,
            scope_id=prepared.scope.id,
            physical_observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw="ROUND2-PENDING-UNKNOWN",
                    material_identifier_type="unknown",
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                    count_method="manual",
                    remark="复盘现场仍待核实",
                ),
            ),
            zero_confirmed=False,
        ),
        idempotency_key="opening-round-two-pending-count",
        request_id="opening-round-two-pending-count-request",
    )
    assert result.round_sealed is True
    observation = world.db.scalar(
        select(StocktakeCountObservation).where(
            StocktakeCountObservation.round_id == prepared.round.id
        )
    )
    assert observation is not None
    assert observation.verification_status == "pending_verification"

    monkeypatch.setattr(
        disposition_service,
        "_database_now",
        lambda _db: NOW + timedelta(hours=4, minutes=30),
    )
    disposed = record_opening_observation_disposition(
        world.db,
        actor=world.principals["manager_x"],
        command=RecordOpeningObservationDispositionCommand(
            task_id=prepared.task.id,
            round_id=prepared.round.id,
            observation_id=observation.id,
            disposition="pending_verification",
            reason_code="round_two_identifier_pending",
            comment="复盘仍无法唯一解析，保留原始证据并继续受控复盘",
        ),
        idempotency_key="opening-round-two-pending-disposition",
        request_id="opening-round-two-pending-disposition-request",
    )
    assert disposed.round_id == prepared.round.id
    assert world.db.get(StocktakeObservationDisposition, disposed.disposition_id) is not None

    prepared.task = world.db.get(FormalStocktakeTask, prepared.task.id)
    prepared.round = world.db.get(StocktakeRound, prepared.round.id)
    review = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(
            world.db,
            prepared,
            decision="recount",
            comment="第二轮待核实观察已规范处置，继续复盘",
        ),
        idempotency_key="opening-round-two-pending-region-recount",
        request_id="opening-round-two-pending-region-recount-request",
    )
    assert review.resulting_task_status == "recount_required"


@pytest.mark.parametrize("approved", [False, True], ids=["submitted", "approved"])
def test_first_recount_replay_reproves_immediate_round_two_without_writes(
    world,
    approved: bool,
) -> None:
    prepared = _first_recount_with_submitted_round_two(
        world,
        key=f"opening-first-recount-immediate-{approved}",
    )
    if approved:
        submit_opening_region_review(
            world.db,
            actor=world.principals["manager_x"],
            command=_review_command(world.db, prepared, decision="approve"),
            idempotency_key="opening-immediate-round-two-region-approve",
            request_id="opening-immediate-round-two-region-approve-request",
        )
        submit_opening_headquarters_review(
            world.db,
            actor=world.principals["admin"],
            command=_review_command(world.db, prepared, decision="approve"),
            idempotency_key="opening-immediate-round-two-hq-approve",
            request_id="opening-immediate-round-two-hq-approve-request",
        )
    world.db.commit()
    before = {
        model: world.db.scalar(select(func.count()).select_from(model)) or 0
        for model in (
            StocktakeRecountCase,
            StocktakeRecountScopeAssignment,
            StocktakeRound,
            StateTransitionEvent,
            OutboxEvent,
            AuditEvent,
        )
    }

    replay = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=prepared.command,
        idempotency_key=prepared.key,
        request_id=f"{prepared.key}-transport-retry",
    )

    assert replay.replayed is True
    assert replay.recount_case_id == prepared.recount_case_id
    assert before == {
        model: world.db.scalar(select(func.count()).select_from(model)) or 0
        for model in before
    }


@pytest.mark.parametrize(
    "tamper_target",
    ["scope_completion", "assignment", "count_outbox"],
)
def test_first_recount_replay_fails_closed_on_immediate_round_two_tamper(
    world,
    tamper_target: str,
) -> None:
    prepared = _first_recount_with_submitted_round_two(
        world,
        key=f"opening-first-recount-immediate-tamper-{tamper_target}",
    )
    world.db.commit()
    if tamper_target == "scope_completion":
        row = world.db.scalar(
            select(StocktakeScopeCountCompletion).where(
                StocktakeScopeCountCompletion.round_id == prepared.round.id,
                StocktakeScopeCountCompletion.scope_id == prepared.scope.id,
            )
        )
        assert row is not None
        row.evidence_manifest_sha256 = "f" * 64
    elif tamper_target == "assignment":
        row = world.db.scalar(
            select(StocktakeRecountScopeAssignment).where(
                StocktakeRecountScopeAssignment.recount_case_id
                == prepared.recount_case_id,
                StocktakeRecountScopeAssignment.scope_id == prepared.scope.id,
            )
        )
        assert row is not None
        row.assignment_sha256 = "f" * 64
    else:
        rows = world.db.scalars(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "stocktake_scope",
                OutboxEvent.aggregate_id == str(prepared.scope.id),
                OutboxEvent.event_type
                == "stocktake.opening.scope_count_completed",
            )
        ).all()
        matching = [
            value
            for value in rows
            if value.payload_jsonb.get("round_id") == str(prepared.round.id)
        ]
        assert len(matching) == 1
        row = matching[0]
        row.available_at = row.available_at + timedelta(seconds=1)
    world.db.commit()

    with pytest.raises(OpeningStocktakeRecountError) as invalid:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=prepared.command,
            idempotency_key=prepared.key,
            request_id=f"{prepared.key}-tampered-retry",
        )

    assert invalid.value.code == "opening_recount_idempotency_record_invalid"


@pytest.mark.parametrize("decision", ["recount", "reject"])
def test_round_two_region_nonapproval_opens_round_three(
    world,
    decision: str,
) -> None:
    prepared = _submitted_round_two(world)
    review = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(
            world.db,
            prepared,
            decision=decision,
            comment=f"区域{decision}并进入下一轮",
        ),
        idempotency_key=f"opening-round-two-region-{decision}",
        request_id=f"opening-round-two-region-{decision}-request",
    )
    assert review.resulting_task_status == "recount_required"

    opened = _open_round_three(
        world,
        prepared,
        key=f"opening-round-three-from-region-{decision}",
    )
    assert opened.next_round_no == 3
    task = world.db.get(FormalStocktakeTask, prepared.task.id)
    round_three = world.db.get(StocktakeRound, opened.next_round_id)
    assert task is not None and task.current_round_no == 3 and task.status == "counting"
    assert round_three is not None and round_three.round_type == "recount"


def test_round_two_headquarters_reject_opens_round_three(world) -> None:
    prepared = _submitted_round_two(world)
    submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(world.db, prepared, decision="approve"),
        idempotency_key="opening-round-two-region-before-hq-reject",
        request_id="opening-round-two-region-before-hq-reject-request",
    )
    headquarters = submit_opening_headquarters_review(
        world.db,
        actor=world.principals["admin"],
        command=_review_command(
            world.db,
            prepared,
            decision="reject",
            comment="总部驳回本轮并要求复盘",
        ),
        idempotency_key="opening-round-two-headquarters-reject",
        request_id="opening-round-two-headquarters-reject-request",
    )
    assert headquarters.resulting_task_status == "recount_required"

    opened = _open_round_three(
        world,
        prepared,
        key="opening-round-three-from-headquarters-reject",
    )
    case = world.db.get(StocktakeRecountCase, opened.recount_case_id)
    assert case is not None and case.trigger_review_id == headquarters.review_id
    assert opened.next_round_no == 3


def test_headquarters_recount_is_rejected_before_any_write(world) -> None:
    prepared = _submitted_round_two(world)
    submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(world.db, prepared, decision="approve"),
        idempotency_key="opening-round-two-region-before-invalid-hq",
        request_id="opening-round-two-region-before-invalid-hq-request",
    )
    before = {
        model: world.db.scalar(select(func.count()).select_from(model)) or 0
        for model in (StocktakeReview, StateTransitionEvent, OutboxEvent, AuditEvent)
    }
    with pytest.raises(OpeningStocktakeReviewError) as invalid:
        submit_opening_headquarters_review(
            world.db,
            actor=world.principals["admin"],
            command=_review_command(
                world.db,
                prepared,
                decision="recount",
                comment="非法总部复盘结论",
            ),
            idempotency_key="opening-round-two-invalid-hq-recount",
            request_id="opening-round-two-invalid-hq-recount-request",
        )
    assert invalid.value.code == "opening_review_headquarters_recount_forbidden"
    assert before == {
        model: world.db.scalar(select(func.count()).select_from(model)) or 0
        for model in before
    }


def test_round_two_review_keeps_region_headquarters_duty_separation(world) -> None:
    prepared = _submitted_round_two(world)
    world.manager_x.person.organization_id = world.hq.id
    world.db.add(
        RoleAssignment(
            id=uuid.uuid4(),
            user_id=world.manager_x.user.id,
            role_id=world.roles["admin"].id,
            scope_type="national",
            scope_id="*",
            valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
            valid_to=None,
            status="active",
            assigned_by=world.manager_x.user.id,
            revoked_at=None,
            revoked_by=None,
            reason="round two duty separation",
        )
    )
    world.manager_x.user.authorization_version += 1
    world.db.commit()
    dual = load_formal_principal(world.db, world.manager_x.user.id, now=NOW)
    command = _review_command(world.db, prepared, decision="approve")
    submit_opening_region_review(
        world.db,
        actor=dual,
        command=command,
        idempotency_key="opening-round-two-dual-region",
        request_id="opening-round-two-dual-region-request",
    )
    with pytest.raises(OpeningStocktakeReviewError) as invalid:
        submit_opening_headquarters_review(
            world.db,
            actor=dual,
            command=command,
            idempotency_key="opening-round-two-dual-headquarters",
            request_id="opening-round-two-dual-headquarters-request",
        )
    assert invalid.value.code == "opening_review_separation_of_duties_required"


def test_first_recount_replay_after_round_three_is_read_only_and_reproves_descendants(
    world,
) -> None:
    task, source_round, scope = _prepare_recount_required(world)
    first_command = _open_recount_command(
        task, source_round, scope, world.manager_x.user.id
    )
    first_key = "opening-first-recount-deep-replay"
    first = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=first_command,
        idempotency_key=first_key,
        request_id="opening-first-recount-deep-replay-request",
    )
    prepared = SimpleNamespace(
        task=world.db.get(FormalStocktakeTask, task.id),
        source_round=source_round,
        round=world.db.get(StocktakeRound, first.next_round_id),
        scope=scope,
        recount_case_id=first.recount_case_id,
    )
    _submit_round_two(
        world,
        prepared,
        actor_name="manager_x",
        key="opening-deep-replay-round-two-count",
    )
    prepared.task = world.db.get(FormalStocktakeTask, task.id)
    prepared.round = world.db.get(StocktakeRound, first.next_round_id)
    submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(
            world.db,
            prepared,
            decision="reject",
            comment="第二轮驳回后打开第三轮",
        ),
        idempotency_key="opening-deep-replay-round-two-reject",
        request_id="opening-deep-replay-round-two-reject-request",
    )
    second = _open_round_three(
        world,
        prepared,
        key="opening-deep-replay-open-round-three",
    )
    world.db.commit()
    before = {
        model: world.db.scalar(select(func.count()).select_from(model)) or 0
        for model in (
            StocktakeRecountCase,
            StocktakeRecountScopeAssignment,
            StocktakeRound,
            StateTransitionEvent,
            OutboxEvent,
            AuditEvent,
        )
    }
    replay = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=first_command,
        idempotency_key=first_key,
        request_id="opening-first-recount-deep-replay-transport-retry",
    )
    assert replay.replayed is True and replay.recount_case_id == first.recount_case_id
    assert before == {
        model: world.db.scalar(select(func.count()).select_from(model)) or 0
        for model in before
    }

    descendant_assignment = world.db.scalar(
        select(StocktakeRecountScopeAssignment).where(
            StocktakeRecountScopeAssignment.recount_case_id
            == second.recount_case_id
        )
    )
    assert descendant_assignment is not None
    descendant_assignment.assignment_sha256 = "f" * 64
    world.db.commit()
    with pytest.raises(OpeningStocktakeRecountError) as invalid:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=first_command,
            idempotency_key=first_key,
            request_id="opening-first-recount-deep-replay-tampered",
        )
    assert invalid.value.code == "opening_recount_idempotency_record_invalid"
