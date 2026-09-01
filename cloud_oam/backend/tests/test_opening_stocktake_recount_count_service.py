from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from itertools import count
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import func, select

import app.formal_services.opening_stocktake_count as count_service
import app.formal_services.opening_observation_disposition as disposition_service
import app.formal_services.opening_stocktake_recount as recount_service
import app.formal_services.opening_stocktake_review as review_service
from app.formal_access import load_formal_principal
from app.formal_services.opening_stocktake_count import (
    OpeningPhysicalObservationInput,
    OpeningStocktakeCountError,
    SubmitOpeningStocktakeScopeCountCommand,
    submit_opening_stocktake_scope_count,
)
from app.formal_services.opening_stocktake_recount import (
    open_opening_stocktake_recount,
)
from app.formal_services.opening_stocktake_review import (
    submit_opening_region_review,
)
from app.foundation_models import AuditEvent, OutboxEvent, RoleAssignment, StateTransitionEvent
from app.inventory_models import (
    InventoryMovement,
    InventoryTransaction,
    StockAccount,
    StockBalance,
)
from app.stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryFreeze,
    InventoryOpeningEstablishment,
    StocktakeCountLine,
    StocktakeDifference,
    StocktakeDifferenceSetCompletion,
    StocktakePosting,
    StocktakeRecountScopeAssignment,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
)

# Reuse the complete service-built opening -> review -> recount fixture.  The
# production code never imports tests; importing the decorated fixtures here
# only registers them for this independent service test module.
from test_opening_stocktake_recount_service import (  # noqa: E402
    NOW,
    _command as _open_recount_command,
    _fixed_database_times,
    _fixed_recount_time,
    _prepare_recount_required,
    db,
    world,
)
from test_opening_stocktake_review_service import _review_command  # noqa: E402


@pytest.fixture(autouse=True)
def _fixed_recount_count_time(
    monkeypatch: pytest.MonkeyPatch,
    _fixed_database_times: None,
    _fixed_recount_time: None,
) -> None:
    review_ticks = count()

    def _clock(db):
        current_round_no = db.scalar(
            select(func.max(FormalStocktakeTask.current_round_no))
        ) or 1
        return NOW + timedelta(hours=(3 * current_round_no) - 2)

    def _review_clock(db):
        current_round_no = db.scalar(
            select(func.max(FormalStocktakeTask.current_round_no))
        ) or 1
        base_hours = (3 * current_round_no) - 1
        return NOW + timedelta(
            hours=base_hours,
            microseconds=next(review_ticks),
        )

    def _recount_clock(db):
        source_round_no = db.scalar(
            select(func.max(FormalStocktakeTask.current_round_no))
        ) or 1
        return NOW + timedelta(hours=3 * source_round_no)

    monkeypatch.setattr(
        count_service,
        "_database_now",
        _clock,
    )
    monkeypatch.setattr(review_service, "_database_now", _review_clock)
    monkeypatch.setattr(recount_service, "_database_now", _recount_clock)


def _inventory_counts(world: SimpleNamespace) -> dict[type[object], int]:
    return {
        model: world.db.scalar(select(func.count()).select_from(model)) or 0
        for model in (
            StockAccount,
            StockBalance,
            InventoryTransaction,
            InventoryMovement,
            StocktakePosting,
            InventoryOpeningEstablishment,
        )
    }


def _open_round_two(
    world: SimpleNamespace,
    *,
    assignee_user_id: str | None = None,
) -> SimpleNamespace:
    task, source_round, scope = _prepare_recount_required(world)
    result = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=_open_recount_command(
            task,
            source_round,
            scope,
            assignee_user_id or world.manager_x.user.id,
        ),
        idempotency_key=f"opening-recount-count-open-{uuid.uuid4().hex}",
        request_id="opening-recount-count-open-request",
    )
    world.db.commit()
    world.db.expire_all()
    task = world.db.get(FormalStocktakeTask, task.id)
    source_round = world.db.get(StocktakeRound, source_round.id)
    round_two = world.db.get(StocktakeRound, result.next_round_id)
    scope = world.db.get(FormalStocktakeScope, scope.id)
    assert task is not None and task.status == "counting"
    assert source_round is not None and source_round.status == "submitted"
    assert round_two is not None and round_two.status == "counting"
    assert scope is not None
    return SimpleNamespace(
        task=task,
        source_round=source_round,
        round=round_two,
        scope=scope,
        recount_case_id=result.recount_case_id,
    )


def _count_command(
    world: SimpleNamespace,
    prepared: SimpleNamespace,
    *,
    qty: str = "3.000",
):
    return SubmitOpeningStocktakeScopeCountCommand(
        task_id=prepared.task.id,
        round_id=prepared.round.id,
        scope_id=prepared.scope.id,
        physical_observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal(qty),
                count_method="manual",
            ),
        ),
        zero_confirmed=False,
    )


def _submit_round_two(
    world: SimpleNamespace,
    prepared: SimpleNamespace,
    *,
    actor_name: str,
    key: str,
):
    return submit_opening_stocktake_scope_count(
        world.db,
        actor=world.principals[actor_name],
        command=_count_command(world, prepared),
        idempotency_key=key,
        request_id="opening-recount-count-request",
    )


def _open_round_three_after_submitted_round_two(
    world: SimpleNamespace,
    *,
    round_three_assignee_user_id: str,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    round_two = _open_round_two(world)
    _submit_round_two(
        world,
        round_two,
        actor_name="manager_x",
        key=f"opening-round-two-before-three-{uuid.uuid4().hex}",
    )
    world.db.commit()
    task = world.db.get(FormalStocktakeTask, round_two.task.id)
    source_round = world.db.get(StocktakeRound, round_two.round.id)
    scope = world.db.get(FormalStocktakeScope, round_two.scope.id)
    assert task is not None and source_round is not None and scope is not None
    submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(
            world.db,
            SimpleNamespace(task=task, round=source_round),
            decision="recount",
            comment="第二轮仍需复盘",
        ),
        idempotency_key=f"opening-round-two-review-{uuid.uuid4().hex}",
        request_id="opening-round-two-review-request",
    )
    world.db.commit()
    task = world.db.get(FormalStocktakeTask, task.id)
    source_round = world.db.get(StocktakeRound, source_round.id)
    scope = world.db.get(FormalStocktakeScope, scope.id)
    assert task is not None and task.status == "recount_required"
    assert source_round is not None and scope is not None
    opened = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=_open_recount_command(
            task,
            source_round,
            scope,
            round_three_assignee_user_id,
        ),
        idempotency_key=f"opening-round-three-open-{uuid.uuid4().hex}",
        request_id="opening-round-three-open-request",
    )
    world.db.commit()
    round_three = SimpleNamespace(
        task=world.db.get(FormalStocktakeTask, task.id),
        source_round=source_round,
        round=world.db.get(StocktakeRound, opened.next_round_id),
        scope=world.db.get(FormalStocktakeScope, scope.id),
        recount_case_id=opened.recount_case_id,
    )
    assert round_three.task is not None and round_three.task.current_round_no == 3
    assert round_three.round is not None and round_three.round.round_no == 3
    assert round_three.scope is not None
    return round_two, round_three


def test_recount_count_locks_one_complete_0027_reference_graph_in_order(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _open_round_two(world)
    calls: list[str] = []
    monkeypatch.setattr(
        disposition_service,
        "lock_opening_stocktake_start_reference",
        lambda *_args, **_kwargs: calls.append("start"),
    )
    monkeypatch.setattr(
        disposition_service,
        "lock_inventory_reference_graph",
        lambda *_args, **_kwargs: calls.append("inventory"),
    )
    monkeypatch.setattr(
        disposition_service,
        "lock_inventory_serial_graph",
        lambda *_args, **_kwargs: calls.append("serial"),
    )

    result = _submit_round_two(
        world,
        prepared,
        actor_name="manager_x",
        key="opening-recount-count-single-reference-graph",
    )

    assert result.round_sealed is True
    assert calls == ["start", "inventory", "serial"]


def test_recount_scope_count_uses_round_assignment_and_seals_independent_round(world):
    prepared = _open_round_two(world)
    before_inventory = _inventory_counts(world)
    source_difference_ids = set(
        world.db.scalars(
            select(StocktakeDifference.id).where(
                StocktakeDifference.round_id == prepared.source_round.id
            )
        ).all()
    )

    result = _submit_round_two(
        world,
        prepared,
        actor_name="manager_x",
        key="opening-recount-count-success",
    )

    assert result.round_sealed is True
    assert result.round_status == "submitted"
    assert result.task_status == "submitted"
    assert _inventory_counts(world) == before_inventory
    assert prepared.source_round.status == "submitted"
    assert source_difference_ids == set(
        world.db.scalars(
            select(StocktakeDifference.id).where(
                StocktakeDifference.round_id == prepared.source_round.id
            )
        ).all()
    )
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeDifference).where(
            StocktakeDifference.round_id == prepared.round.id
        )
    )
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeDifferenceSetCompletion).where(
            StocktakeDifferenceSetCompletion.round_id == prepared.round.id
        )
    ) == 1
    completion = world.db.scalar(
        select(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.round_id == prepared.round.id
        )
    )
    snapshot = world.db.scalar(
        select(StocktakeRecountScopeAssignment).where(
            StocktakeRecountScopeAssignment.recount_case_id
            == prepared.recount_case_id,
            StocktakeRecountScopeAssignment.scope_id == prepared.scope.id,
        )
    )
    assert completion is not None and snapshot is not None
    assert completion.completed_by_user_id == snapshot.assignee_user_id
    assert completion.completed_by_person_id == snapshot.assignee_person_id
    assert (
        completion.completed_role_assignment_id
        == snapshot.assignee_role_assignment_id
    )
    assert world.db.scalar(
        select(func.count()).select_from(StateTransitionEvent).where(
            StateTransitionEvent.reason == "opening_recount_round_submitted",
            StateTransitionEvent.aggregate_id == str(prepared.round.id),
        )
    ) == 1
    outbox = world.db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.event_type == "stocktake.opening.round_submitted",
            OutboxEvent.aggregate_id == str(prepared.round.id),
        )
    )
    audit = world.db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "stocktake.opening.round_submitted",
            AuditEvent.aggregate_id == str(prepared.round.id),
        )
    )
    assert outbox is not None and audit is not None
    for document in (outbox.payload_jsonb, audit.after_jsonb):
        assert document["round_id"] == str(prepared.round.id)
        assert document["round_no"] == 2
        assert document["round_type"] == "recount"
        assert document["recount_case_id"] == str(prepared.recount_case_id)


def test_recount_rejects_base_scope_assignee_when_snapshot_names_admin(world):
    prepared = _open_round_two(
        world,
        assignee_user_id=world.admin.user.id,
    )
    assert prepared.scope.assignee_user_id == world.manager_x.user.id

    with pytest.raises(OpeningStocktakeCountError) as denied:
        _submit_round_two(
            world,
            prepared,
            actor_name="manager_x",
            key="opening-recount-count-base-assignee-denied",
        )
    assert denied.value.code == "opening_count_not_round_assignee"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeCountLine).where(
            StocktakeCountLine.round_id == prepared.round.id
        )
    ) == 0
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.round_id == prepared.round.id
        )
    ) == 0

    result = _submit_round_two(
        world,
        prepared,
        actor_name="admin",
        key="opening-recount-count-snapshot-admin",
    )
    assert result.round_sealed is True


def test_recount_revalidates_snapshot_role_assignment_as_current_before_writing(world):
    prepared = _open_round_two(world)
    snapshot = world.db.scalar(
        select(StocktakeRecountScopeAssignment).where(
            StocktakeRecountScopeAssignment.recount_case_id
            == prepared.recount_case_id
        )
    )
    assert snapshot is not None
    assignment = world.db.get(RoleAssignment, snapshot.assignee_role_assignment_id)
    assert assignment is not None
    revoker_id = world.admin.user.id
    assignment.status = "revoked"
    assignment.revoked_at = NOW + timedelta(hours=3, minutes=30)
    assignment.revoked_by = revoker_id
    world.db.commit()

    with pytest.raises(OpeningStocktakeCountError) as denied:
        _submit_round_two(
            world,
            prepared,
            actor_name="manager_x",
            key="opening-recount-count-revoked-current-role",
        )
    assert denied.value.code == "opening_count_actor_not_current"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeCountLine).where(
            StocktakeCountLine.round_id == prepared.round.id
        )
    ) == 0
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.round_id == prepared.round.id
        )
    ) == 0


def test_recount_same_key_replays_after_review_and_round_exact_sets_coexist(world):
    prepared = _open_round_two(world)
    key = "opening-recount-count-downstream-replay"
    first = _submit_round_two(
        world,
        prepared,
        actor_name="manager_x",
        key=key,
    )
    with pytest.raises(OpeningStocktakeCountError) as different_key:
        _submit_round_two(
            world,
            prepared,
            actor_name="manager_x",
            key="opening-recount-count-downstream-different-key",
        )
    assert different_key.value.code == "opening_count_scope_already_completed"
    world.db.commit()
    task = world.db.get(FormalStocktakeTask, prepared.task.id)
    round_two = world.db.get(StocktakeRound, prepared.round.id)
    assert task is not None and round_two is not None
    submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(
            world.db,
            SimpleNamespace(task=task, round=round_two),
            decision="approve",
        ),
        idempotency_key="opening-recount-count-region-approved",
        request_id="opening-recount-count-region-approved-request",
    )
    world.db.commit()
    counts_before = {
        model: world.db.scalar(select(func.count()).select_from(model)) or 0
        for model in (
            StocktakeCountLine,
            StocktakeScopeCountCompletion,
            StocktakeRoundSubmission,
            StocktakeDifferenceSetCompletion,
            StocktakeDifference,
            StateTransitionEvent,
            OutboxEvent,
            AuditEvent,
        )
    }

    replay = _submit_round_two(
        world,
        prepared,
        actor_name="manager_x",
        key=key,
    )

    assert replay.replayed is True
    assert replay.task_status == "hq_review"
    assert replay.round_id == first.round_id
    assert counts_before == {
        model: world.db.scalar(select(func.count()).select_from(model)) or 0
        for model in counts_before
    }


def test_recount_replay_rejects_alternate_key_duplicate_for_same_round(world):
    prepared = _open_round_two(world)
    key = "opening-recount-count-exact-set"
    _submit_round_two(
        world,
        prepared,
        actor_name="manager_x",
        key=key,
    )
    round_outbox = world.db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.event_type == "stocktake.opening.round_submitted",
            OutboxEvent.aggregate_id == str(prepared.round.id),
        )
    )
    assert round_outbox is not None
    world.db.add(
        OutboxEvent(
            event_type=round_outbox.event_type,
            aggregate_type=round_outbox.aggregate_type,
            aggregate_id=round_outbox.aggregate_id,
            payload_jsonb=dict(round_outbox.payload_jsonb),
            status="pending",
            attempts=0,
            idempotency_key=f"alternate-{uuid.uuid4().hex}",
            available_at=round_outbox.available_at,
            locked_at=None,
            locked_by=None,
            published_at=None,
            last_error=None,
            created_at=round_outbox.created_at,
            updated_at=round_outbox.updated_at,
        )
    )
    world.db.flush()

    with pytest.raises(OpeningStocktakeCountError) as invalid:
        _submit_round_two(
            world,
            prepared,
            actor_name="manager_x",
            key=key,
        )
    assert invalid.value.code == "opening_count_recount_evidence_invalid"


def test_round_three_again_uses_its_own_assignment_snapshot_not_base_scope(world):
    round_two, round_three = _open_round_three_after_submitted_round_two(
        world,
        round_three_assignee_user_id=world.admin.user.id,
    )
    assert round_three.scope.assignee_user_id == world.manager_x.user.id

    with pytest.raises(OpeningStocktakeCountError) as base_denied:
        _submit_round_two(
            world,
            round_three,
            actor_name="manager_x",
            key="opening-round-three-base-assignee-denied",
        )
    assert base_denied.value.code == "opening_count_not_round_assignee"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.round_id == round_three.round.id
        )
    ) == 0

    accepted = _submit_round_two(
        world,
        round_three,
        actor_name="admin",
        key="opening-round-three-snapshot-admin-accepted",
    )
    assert accepted.round_sealed is True
    for prepared, expected_user_id in (
        (round_two, world.manager_x.user.id),
        (round_three, world.admin.user.id),
    ):
        snapshot = world.db.scalar(
            select(StocktakeRecountScopeAssignment).where(
                StocktakeRecountScopeAssignment.recount_case_id
                == prepared.recount_case_id,
                StocktakeRecountScopeAssignment.scope_id == prepared.scope.id,
            )
        )
        completion = world.db.scalar(
            select(StocktakeScopeCountCompletion).where(
                StocktakeScopeCountCompletion.round_id == prepared.round.id,
                StocktakeScopeCountCompletion.scope_id == prepared.scope.id,
            )
        )
        assert snapshot is not None and completion is not None
        assert snapshot.assignee_user_id == expected_user_id
        assert completion.completed_by_user_id == expected_user_id
        assert (
            completion.completed_role_assignment_id
            == snapshot.assignee_role_assignment_id
        )


def test_round_three_count_fails_closed_if_previous_edge_assignment_is_tampered(world):
    round_two, round_three = _open_round_three_after_submitted_round_two(
        world,
        round_three_assignee_user_id=world.admin.user.id,
    )
    previous_assignment = world.db.scalar(
        select(StocktakeRecountScopeAssignment).where(
            StocktakeRecountScopeAssignment.recount_case_id
            == round_two.recount_case_id,
            StocktakeRecountScopeAssignment.scope_id == round_two.scope.id,
        )
    )
    assert previous_assignment is not None
    previous_assignment.assignment_sha256 = "f" * 64
    world.db.flush()

    with pytest.raises(OpeningStocktakeCountError) as invalid_chain:
        _submit_round_two(
            world,
            round_three,
            actor_name="admin",
            key="opening-round-three-previous-edge-tampered",
        )
    assert invalid_chain.value.code == "opening_count_recount_evidence_invalid"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeCountLine).where(
            StocktakeCountLine.round_id == round_three.round.id
        )
    ) == 0
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.round_id == round_three.round.id
        )
    ) == 0


def test_recount_completion_accepts_newer_current_authorization_version(world):
    prepared = _open_round_two(world)
    snapshot = world.db.scalar(
        select(StocktakeRecountScopeAssignment).where(
            StocktakeRecountScopeAssignment.recount_case_id
            == prepared.recount_case_id
        )
    )
    assert snapshot is not None
    manager_user = world.manager_x.user
    manager_user.authorization_version += 1
    world.db.commit()
    world.principals["manager_x"] = load_formal_principal(
        world.db,
        manager_user.id,
        now=NOW + timedelta(hours=4),
    )

    result = _submit_round_two(
        world,
        prepared,
        actor_name="manager_x",
        key="opening-recount-count-newer-auth-version",
    )

    assert result.round_sealed is True
    completion = world.db.scalar(
        select(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.round_id == prepared.round.id
        )
    )
    assert completion is not None
    assert completion.authorization_version > snapshot.authorization_version


def test_completion_validator_requires_round_assignment_only_for_recount(world):
    prepared = _open_round_two(world)
    _submit_round_two(
        world,
        prepared,
        actor_name="manager_x",
        key="opening-recount-count-mutual-assignment-parameter",
    )
    initial_completion = world.db.scalar(
        select(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.round_id == prepared.source_round.id
        )
    )
    recount_completion = world.db.scalar(
        select(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.round_id == prepared.round.id
        )
    )
    snapshot = world.db.scalar(
        select(StocktakeRecountScopeAssignment).where(
            StocktakeRecountScopeAssignment.recount_case_id
            == prepared.recount_case_id
        )
    )
    assert initial_completion is not None
    assert recount_completion is not None
    assert snapshot is not None

    with pytest.raises(OpeningStocktakeCountError) as initial_with_recount:
        count_service._validate_completion_evidence(
            world.db,
            prepared.task,
            prepared.source_round,
            prepared.scope,
            initial_completion,
            expected_recount_assignment=snapshot,
        )
    assert initial_with_recount.value.code == "opening_count_replay_evidence_invalid"

    with pytest.raises(OpeningStocktakeCountError) as recount_without_assignment:
        count_service._validate_completion_evidence(
            world.db,
            prepared.task,
            prepared.round,
            prepared.scope,
            recount_completion,
        )
    assert (
        recount_without_assignment.value.code
        == "opening_count_replay_evidence_invalid"
    )


@pytest.mark.parametrize("status", ("posted", "closed"))
def test_downstream_anchor_accepts_only_proven_released_freeze(world, status):
    prepared = _open_round_two(world)
    _submit_round_two(
        world,
        prepared,
        actor_name="manager_x",
        key=f"opening-recount-count-{status}-freeze",
    )
    posted_at = NOW + timedelta(hours=6)
    prepared.task.status = status
    prepared.task.posted_at = posted_at
    prepared.task.closed_at = (
        posted_at + timedelta(minutes=1) if status == "closed" else None
    )
    freeze = world.db.scalar(
        select(InventoryFreeze).where(
            InventoryFreeze.task_id == prepared.task.id,
            InventoryFreeze.stocktake_scope_id == prepared.scope.id,
        )
    )
    assert freeze is not None
    freeze.status = "released"
    freeze.valid_to = posted_at
    freeze.released_by_user_id = world.admin.user.id
    freeze.release_reason = "期初入账已完成"
    world.db.flush()
    scopes = tuple(
        world.db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == prepared.task.id)
            .order_by(FormalStocktakeScope.scope_no)
        ).all()
    )

    count_service._validate_start_anchors(
        world.db,
        prepared.task,
        prepared.round,
        scopes,
        (freeze,),
        NOW + timedelta(hours=7),
        allow_downstream=True,
    )

    freeze.valid_to = posted_at - timedelta(microseconds=1)
    world.db.flush()
    with pytest.raises(OpeningStocktakeCountError) as invalid_release:
        count_service._validate_start_anchors(
            world.db,
            prepared.task,
            prepared.round,
            scopes,
            (freeze,),
            NOW + timedelta(hours=7),
            allow_downstream=True,
        )
    assert invalid_release.value.code == "opening_count_scope_anchor_invalid"
