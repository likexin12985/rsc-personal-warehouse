from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import inspect
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import func, select

import app.formal_services.opening_stocktake_recount as recount_service
import app.formal_services.opening_observation_disposition as disposition_service
from app.formal_services.inventory_posting import (
    canonical_opening_scope_manifest_sha256,
)
from app.formal_services.opening_stocktake_recount import (
    OpenOpeningStocktakeRecountCommand,
    OpeningStocktakeRecountError,
    OpeningStocktakeRecountScopeAssignmentInput,
    open_opening_stocktake_recount,
)
from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent
from app.inventory_models import (
    CustodyAssignment,
    InventoryMovement,
    InventoryTransaction,
    StockAccount,
    StockBalance,
    StockLocation,
)
from app.formal_services.opening_stocktake import OpeningStocktakeScopeInput
from app.stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryFreeze,
    InventoryOpeningEstablishment,
    StocktakePosting,
    StocktakeCountLine,
    StocktakeRecountCase,
    StocktakeRecountScopeAssignment,
    StocktakeReview,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
)

# Reuse the complete, service-built opening/count/review fixture.  Importing
# the decorated fixtures into this module makes pytest register them here too;
# no production module depends on test code.
from test_opening_stocktake_review_service import (  # noqa: E402
    NOW,
    _fixed_database_times,
    _prepare_submitted,
    _review_command,
    db,
    world,
)
from app.formal_services.opening_stocktake_review import (  # noqa: E402
    submit_opening_region_review,
)


@pytest.fixture(autouse=True)
def _fixed_recount_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        recount_service,
        "_database_now",
        lambda _db: NOW + timedelta(hours=3),
    )


def _prepare_recount_required(world):
    prepared = _prepare_submitted(world)
    submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(
            world.db,
            prepared,
            decision="recount",
            comment="区域明确要求复盘",
        ),
        idempotency_key=f"opening-region-recount-{uuid.uuid4().hex}",
        request_id="opening-region-recount-request",
    )
    world.db.commit()
    world.db.expire_all()
    task = world.db.get(FormalStocktakeTask, prepared.task.id)
    source_round = world.db.get(StocktakeRound, prepared.round.id)
    scope = world.db.get(FormalStocktakeScope, prepared.scope.id)
    assert task is not None and task.status == "recount_required"
    assert source_round is not None and source_round.status == "submitted"
    assert scope is not None
    return task, source_round, scope


def _command(task, source_round, scope, assignee_user_id):
    return OpenOpeningStocktakeRecountCommand(
        task_id=task.id,
        source_round_id=source_round.id,
        assignments=(
            OpeningStocktakeRecountScopeAssignmentInput(
                scope_id=scope.id,
                assignee_user_id=assignee_user_id,
            ),
        ),
        reason="区域复核已确认需要重新实盘",
    )


def _inventory_counts(db):
    return {
        model: db.scalar(select(func.count()).select_from(model)) or 0
        for model in (
            StockAccount,
            StockBalance,
            InventoryTransaction,
            InventoryMovement,
            StocktakePosting,
            InventoryOpeningEstablishment,
        )
    }


def test_open_recount_locks_one_complete_0027_reference_graph_in_order(
    world,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, source_round, scope = _prepare_recount_required(world)
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

    result = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=_command(task, source_round, scope, world.manager_x.user.id),
        idempotency_key="opening-recount-single-reference-graph",
        request_id="opening-recount-single-reference-graph-request",
    )

    assert result.replayed is False
    assert calls == ["start", "inventory", "serial"]


def test_open_recount_success_preserves_source_and_writes_complete_independent_evidence(world):
    task, source_round, scope = _prepare_recount_required(world)
    before_inventory = _inventory_counts(world.db)
    old_task_version = task.version

    result = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=_command(task, source_round, scope, world.manager_x.user.id),
        idempotency_key="opening-recount-success-0001",
        request_id="opening-recount-success-request",
    )

    assert result.next_round_no == 2
    assert result.scope_count == 1
    assert result.replayed is False
    assert source_round.status == "submitted"
    assert source_round.round_type == "initial"
    assert source_round.recount_case_id is None
    assert task.status == "counting"
    assert task.current_round_no == 2
    assert task.version == old_task_version + 1
    next_round = world.db.get(StocktakeRound, result.next_round_id)
    assert next_round is not None
    assert next_round.round_type == "recount"
    assert next_round.status == "counting"
    assert next_round.recount_case_id == result.recount_case_id
    case = world.db.get(StocktakeRecountCase, result.recount_case_id)
    assert case is not None
    assignment = world.db.scalar(
        select(StocktakeRecountScopeAssignment).where(
            StocktakeRecountScopeAssignment.recount_case_id == case.id
        )
    )
    assert assignment is not None
    assert assignment.scope_id == scope.id
    assert assignment.role_code == "provincial_manager"
    assert assignment.scope_id_snapshot == str(scope.owner_org_id)
    assert _inventory_counts(world.db) == before_inventory
    assert world.db.scalar(
        select(func.count()).select_from(StateTransitionEvent).where(
            StateTransitionEvent.reason == "opening_recount_opened"
        )
    ) == 1
    assert world.db.scalar(
        select(func.count()).select_from(OutboxEvent).where(
            OutboxEvent.event_type == "stocktake.opening.recount_opened"
        )
    ) == 1
    assert world.db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == "stocktake.opening.recount_opened"
        )
    ) == 1


def test_exact_replay_is_read_only_and_conflict_or_tamper_fails_closed(world):
    task, source_round, scope = _prepare_recount_required(world)
    command = _command(task, source_round, scope, world.manager_x.user.id)
    key = "opening-recount-replay-0001"
    first = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=key,
        request_id="opening-recount-replay-request",
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
        command=command,
        idempotency_key=key,
        request_id="a-different-transport-request",
    )
    assert replay.replayed is True
    assert replay.recount_case_id == first.recount_case_id
    assert replay.next_round_id == first.next_round_id
    assert before == {
        model: world.db.scalar(select(func.count()).select_from(model)) or 0
        for model in before
    }

    # The same globally unique key pointed at another existing source round
    # must conflict before that (counting) round reaches common-graph checks.
    with pytest.raises(OpeningStocktakeRecountError) as source_conflict:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=replace(command, source_round_id=first.next_round_id),
            idempotency_key=key,
            request_id="opening-recount-other-source-request",
        )
    assert source_conflict.value.code == "opening_recount_idempotency_conflict"

    with pytest.raises(OpeningStocktakeRecountError) as conflict:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=replace(command, reason="同键不同请求"),
            idempotency_key=key,
            request_id="opening-recount-conflict-request",
        )
    assert conflict.value.code == "opening_recount_idempotency_conflict"

    world.db.rollback()
    assignment = world.db.scalar(
        select(StocktakeRecountScopeAssignment).where(
            StocktakeRecountScopeAssignment.recount_case_id == first.recount_case_id
        )
    )
    assert assignment is not None
    assignment.assignment_sha256 = "f" * 64
    world.db.commit()
    with pytest.raises(OpeningStocktakeRecountError) as tampered:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=command,
            idempotency_key=key,
            request_id="opening-recount-tamper-request",
        )
    assert tampered.value.code == "opening_recount_idempotency_record_invalid"


def test_admin_assignee_allowed_but_technician_cannot_count_region_scope(world):
    task, source_round, scope = _prepare_recount_required(world)
    result = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=_command(task, source_round, scope, world.admin.user.id),
        idempotency_key="opening-recount-admin-assignee",
        request_id="opening-recount-admin-request",
    )
    row = world.db.scalar(
        select(StocktakeRecountScopeAssignment).where(
            StocktakeRecountScopeAssignment.recount_case_id == result.recount_case_id
        )
    )
    assert row is not None and row.role_code == "admin"

    world.db.rollback()
    task = world.db.get(FormalStocktakeTask, task.id)
    source_round = world.db.get(StocktakeRound, source_round.id)
    scope = world.db.get(FormalStocktakeScope, scope.id)
    assert task is not None and task.status == "recount_required"
    assert source_round is not None and source_round.status == "submitted"
    assert scope is not None
    with pytest.raises(OpeningStocktakeRecountError) as forbidden:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=_command(task, source_round, scope, world.technician.user.id),
            idempotency_key="opening-recount-technician-region",
            request_id="opening-recount-technician-request",
        )
    assert forbidden.value.code == "opening_recount_assignee_scope_forbidden"


def test_technician_may_be_assigned_only_to_exact_personal_custodian_scope(world):
    personal_location = StockLocation(
        id=uuid.uuid4(),
        code="PERSONAL-TECH-RECOUNT",
        name="工程师复盘个人仓",
        location_type="personal",
        owner_org_id=world.region_x.id,
        parent_id=world.location.id,
        custodian_person_id=world.technician.person.id,
        status="active",
    )
    personal_account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=world.region_x.id,
        custodian_person_id=world.technician.person.id,
        location_id=personal_location.id,
        material_id=world.material.id,
        condition_code="new",
        availability_bucket="available",
        lot_id=None,
    )
    world.db.add(personal_location)
    world.db.flush()
    world.db.add(
        CustodyAssignment(
            id=uuid.uuid4(),
            location_id=personal_location.id,
            custodian_person_id=world.technician.person.id,
            valid_from=NOW - timedelta(days=30),
            valid_to=None,
            handover_case_id=None,
        )
    )
    world.db.add(personal_account)
    world.db.flush()
    world.db.add(
        StockBalance(
            stock_account_id=personal_account.id,
            quantity=Decimal("0.000"),
            ledger_cursor=0,
            version=1,
        )
    )
    world.db.commit()
    opening_command = replace(
        world.command,
        task_no="OPEN-RECOUNT-PERSONAL-001",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=personal_location.id,
                assignee_user_id=world.technician.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    prepared = _prepare_submitted(
        world,
        command=opening_command,
        count_actor=world.principals["technician"],
    )
    submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(
            world.db,
            prepared,
            decision="recount",
            comment="个人仓需要复盘",
        ),
        idempotency_key="opening-personal-region-recount",
        request_id="opening-personal-review-request",
    )
    world.db.commit()
    result = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=_command(
            prepared.task,
            prepared.round,
            prepared.scope,
            world.technician.user.id,
        ),
        idempotency_key="opening-personal-recount-open",
        request_id="opening-personal-recount-request",
    )
    assignment = world.db.scalar(
        select(StocktakeRecountScopeAssignment).where(
            StocktakeRecountScopeAssignment.recount_case_id == result.recount_case_id
        )
    )
    assert assignment is not None
    assert assignment.role_code == "technician"
    assert assignment.assignee_person_id == world.technician.person.id
    assert assignment.scope_id_snapshot == str(world.technician.person.id)


def test_cross_owner_manager_and_missing_recount_review_are_blocked(world):
    prepared = _prepare_submitted(world)
    with pytest.raises(OpeningStocktakeRecountError) as no_review:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=_command(
                prepared.task,
                prepared.round,
                prepared.scope,
                world.manager_x.user.id,
            ),
            idempotency_key="opening-recount-without-review",
            request_id="opening-recount-no-review-request",
        )
    assert no_review.value.code == "opening_recount_review_required"
    world.db.rollback()

    task = world.db.get(FormalStocktakeTask, prepared.task.id)
    source_round = world.db.get(StocktakeRound, prepared.round.id)
    scope = world.db.get(FormalStocktakeScope, prepared.scope.id)
    assert task is not None and source_round is not None and scope is not None
    submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(
            world.db,
            SimpleNamespace(task=task, round=source_round),
            decision="recount",
            comment="要求复盘",
        ),
        idempotency_key="opening-cross-owner-region-review",
        request_id="opening-cross-owner-review-request",
    )
    world.db.commit()
    with pytest.raises(OpeningStocktakeRecountError) as cross_owner:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=_command(task, source_round, scope, world.manager_y.user.id),
            idempotency_key="opening-cross-owner-assignee",
            request_id="opening-cross-owner-request",
        )
    assert cross_owner.value.code == "opening_recount_assignee_scope_forbidden"


def test_posting_on_source_blocks_recount_and_later_assignee_auth_version_keeps_history_verifiable(world):
    task, source_round, scope = _prepare_recount_required(world)
    world.db.add(
        StocktakePosting(
            id=uuid.uuid4(),
            task_id=task.id,
            round_id=source_round.id,
            posting_kind="opening",
            inventory_transaction_id=None,
            total_quantity=Decimal("0.000"),
            idempotency_key_hash="a" * 64,
            request_hash="b" * 64,
            posted_by_user_id=world.admin.user.id,
            posted_at=NOW + timedelta(hours=2, minutes=30),
            created_at=NOW + timedelta(hours=2, minutes=30),
        )
    )
    world.db.commit()
    with pytest.raises(OpeningStocktakeRecountError) as posted:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=_command(task, source_round, scope, world.manager_x.user.id),
            idempotency_key="opening-recount-posted-source",
            request_id="opening-recount-posted-request",
        )
    assert posted.value.code == "opening_recount_source_already_posted"


def test_replay_uses_historical_assignee_version_not_current_equality(world):
    task, source_round, scope = _prepare_recount_required(world)
    command = _command(task, source_round, scope, world.admin.user.id)
    key = "opening-recount-historical-auth"
    first = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=key,
        request_id="opening-recount-history-request",
    )
    world.db.commit()
    admin_user = world.db.get(type(world.admin.user), world.admin.user.id)
    assert admin_user is not None
    admin_user.authorization_version += 1
    world.db.commit()
    replay = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=key,
        request_id="opening-recount-history-retry",
    )
    assert replay.replayed is True
    assert replay.recount_case_id == first.recount_case_id


@pytest.mark.parametrize(
    "tamper_target",
    ("count_line", "submission_round_manifest", "scope_completion"),
)
def test_source_submitted_seal_tampering_blocks_recount(world, tamper_target):
    task, source_round, scope = _prepare_recount_required(world)
    if tamper_target == "count_line":
        row = world.db.scalar(
            select(StocktakeCountLine).where(
                StocktakeCountLine.task_id == task.id,
                StocktakeCountLine.round_id == source_round.id,
            )
        )
        assert row is not None
        row.counted_qty += Decimal("1.000")
    elif tamper_target == "submission_round_manifest":
        row = world.db.scalar(
            select(StocktakeRoundSubmission).where(
                StocktakeRoundSubmission.task_id == task.id,
                StocktakeRoundSubmission.round_id == source_round.id,
            )
        )
        assert row is not None
        row.round_manifest_sha256 = "f" * 64
    else:
        row = world.db.scalar(
            select(StocktakeScopeCountCompletion).where(
                StocktakeScopeCountCompletion.task_id == task.id,
                StocktakeScopeCountCompletion.round_id == source_round.id,
                StocktakeScopeCountCompletion.scope_id == scope.id,
            )
        )
        assert row is not None
        row.evidence_manifest_sha256 = "e" * 64
    world.db.commit()

    with pytest.raises(OpeningStocktakeRecountError) as invalid:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=_command(task, source_round, scope, world.manager_x.user.id),
            idempotency_key=f"opening-recount-source-seal-{tamper_target}",
            request_id=f"opening-recount-source-seal-{tamper_target}-request",
        )
    assert invalid.value.code == "opening_recount_source_submitted_seal_invalid"


@pytest.mark.parametrize(
    "tamper_target",
    [
        "stage",
        "authorization",
        "state",
        "state_duplicate",
        "outbox",
        "outbox_duplicate",
        "audit",
    ],
)
def test_trigger_review_is_fully_reproved_before_recount(world, tamper_target):
    task, source_round, scope = _prepare_recount_required(world)
    review = world.db.scalar(
        select(StocktakeReview).where(
            StocktakeReview.task_id == task.id,
            StocktakeReview.round_id == source_round.id,
        )
    )
    assert review is not None
    if tamper_target == "stage":
        review.review_stage = "headquarters"
    elif tamper_target == "authorization":
        review.authorization_version += 1
    elif tamper_target == "state":
        state = world.db.scalar(
            select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_id == str(task.id),
                StateTransitionEvent.reason == "opening_region_review_recount",
            )
        )
        assert state is not None
        state.from_status = "hq_review"
    elif tamper_target == "state_duplicate":
        state = world.db.scalar(
            select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_id == str(task.id),
                StateTransitionEvent.reason == "opening_region_review_recount",
            )
        )
        assert state is not None
        world.db.add(
            StateTransitionEvent(
                aggregate_type=state.aggregate_type,
                aggregate_id=state.aggregate_id,
                from_status=state.from_status,
                to_status=state.to_status,
                reason=state.reason,
                actor_id=state.actor_id,
                idempotency_key=f"alternate-review-state-{uuid.uuid4().hex}",
                occurred_at=state.occurred_at,
                metadata_jsonb=dict(state.metadata_jsonb),
                created_at=state.created_at,
            )
        )
    elif tamper_target == "outbox":
        outbox = world.db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == str(task.id),
                OutboxEvent.event_type == "stocktake.opening.region_reviewed",
            )
        )
        assert outbox is not None
        outbox.available_at = review.reviewed_at + timedelta(seconds=1)
    elif tamper_target == "outbox_duplicate":
        outbox = world.db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_id == str(task.id),
                OutboxEvent.event_type == "stocktake.opening.region_reviewed",
            )
        )
        assert outbox is not None
        world.db.add(
            OutboxEvent(
                event_type=outbox.event_type,
                aggregate_type=outbox.aggregate_type,
                aggregate_id=outbox.aggregate_id,
                payload_jsonb=dict(outbox.payload_jsonb),
                status=outbox.status,
                attempts=outbox.attempts,
                idempotency_key=f"alternate-review-outbox-{uuid.uuid4().hex}",
                available_at=outbox.available_at,
                locked_at=outbox.locked_at,
                locked_by=outbox.locked_by,
                published_at=outbox.published_at,
                last_error=outbox.last_error,
                created_at=outbox.created_at,
                updated_at=outbox.updated_at,
            )
        )
    else:
        audit = world.db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "stocktake.opening.region_reviewed",
                AuditEvent.aggregate_id == str(review.id),
            )
        )
        assert audit is not None
        audit.after_jsonb = {**audit.after_jsonb, "stage": "headquarters"}
    world.db.commit()

    with pytest.raises(OpeningStocktakeRecountError) as invalid:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=_command(task, source_round, scope, world.manager_x.user.id),
            idempotency_key=f"opening-recount-review-tamper-{tamper_target}",
            request_id=f"opening-recount-review-tamper-{tamper_target}-request",
        )
    assert invalid.value.code == "opening_recount_trigger_review_invalid"


def test_technician_is_rejected_for_real_region_location_even_with_bad_custodian_snapshot(world):
    task, source_round, scope = _prepare_recount_required(world)
    del task, source_round
    location = world.db.get(StockLocation, scope.location_id)
    assert location is not None and location.location_type == "region"
    location.custodian_person_id = world.technician.person.id
    scope.custodian_person_id_snapshot = world.technician.person.id
    world.db.flush()

    with pytest.raises(OpeningStocktakeRecountError) as forbidden:
        recount_service._authorize_scope_assignee(
            world.db,
            user_id=world.technician.user.id,
            scope=scope,
            location=location,
            now=NOW + timedelta(hours=3),
        )
    assert forbidden.value.code == "opening_recount_assignee_scope_forbidden"


def test_region_custody_snapshot_is_valid_but_never_grants_technician_recount(world):
    world.location.custodian_person_id = world.technician.person.id
    world.db.add(
        CustodyAssignment(
            id=uuid.uuid4(),
            location_id=world.location.id,
            custodian_person_id=world.technician.person.id,
            valid_from=NOW - timedelta(days=30),
            valid_to=None,
            handover_case_id=None,
        )
    )
    world.db.commit()
    task, source_round, scope = _prepare_recount_required(world)
    assert scope.custodian_person_id_snapshot == world.technician.person.id

    with pytest.raises(OpeningStocktakeRecountError) as forbidden:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=_command(task, source_round, scope, world.technician.user.id),
            idempotency_key="opening-recount-region-custody-technician",
            request_id="opening-recount-region-custody-technician-request",
        )
    assert forbidden.value.code == "opening_recount_assignee_scope_forbidden"

    world.db.rollback()
    task = world.db.get(FormalStocktakeTask, task.id)
    source_round = world.db.get(StocktakeRound, source_round.id)
    scope = world.db.get(FormalStocktakeScope, scope.id)
    assert task is not None and source_round is not None and scope is not None
    result = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=_command(task, source_round, scope, world.manager_x.user.id),
        idempotency_key="opening-recount-region-custody-manager",
        request_id="opening-recount-region-custody-manager-request",
    )
    assert result.next_round_no == 2


def test_exact_replay_revalidates_the_full_source_submitted_seal(world):
    task, source_round, scope = _prepare_recount_required(world)
    command = _command(task, source_round, scope, world.manager_x.user.id)
    key = "opening-recount-replay-source-seal"
    open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=key,
        request_id="opening-recount-replay-seal-request",
    )
    world.db.commit()
    count_line = world.db.scalar(
        select(StocktakeCountLine).where(
            StocktakeCountLine.task_id == task.id,
            StocktakeCountLine.round_id == source_round.id,
        )
    )
    assert count_line is not None
    count_line.remark = "tampered after recount opened"
    world.db.commit()

    with pytest.raises(OpeningStocktakeRecountError) as invalid:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=command,
            idempotency_key=key,
            request_id="opening-recount-replay-seal-retry",
        )
    assert invalid.value.code == "opening_recount_source_submitted_seal_invalid"


def test_missing_scope_posted_source_and_rollback_are_fail_closed(world):
    task, source_round, scope = _prepare_recount_required(world)
    with pytest.raises(OpeningStocktakeRecountError) as missing:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=OpenOpeningStocktakeRecountCommand(
                task_id=task.id,
                source_round_id=source_round.id,
                assignments=(),
                reason="缺范围",
            ),
            idempotency_key="opening-recount-missing-scope",
            request_id="opening-recount-missing-request",
        )
    assert missing.value.code == "opening_recount_assignments_invalid"

    before_cases = world.db.scalar(select(func.count()).select_from(StocktakeRecountCase))
    open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=_command(task, source_round, scope, world.manager_x.user.id),
        idempotency_key="opening-recount-rollback-0001",
        request_id="opening-recount-rollback-request",
    )
    world.db.rollback()
    assert world.db.scalar(select(func.count()).select_from(StocktakeRecountCase)) == before_cases
    restored = world.db.get(FormalStocktakeTask, task.id)
    assert restored is not None and restored.status == "recount_required"
    assert restored.current_round_no == 1


def test_replay_rejects_task_pointer_freeze_and_effect_tampering(world):
    task, source_round, scope = _prepare_recount_required(world)
    command = _command(task, source_round, scope, world.manager_x.user.id)
    key = "opening-recount-pointer-replay"
    result = open_opening_stocktake_recount(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=key,
        request_id="opening-recount-pointer-request",
    )
    world.db.commit()

    task = world.db.get(FormalStocktakeTask, task.id)
    assert task is not None
    task.current_round_no = 99
    task.status = "cancelled"
    world.db.commit()
    with pytest.raises(OpeningStocktakeRecountError) as pointer:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=command,
            idempotency_key=key,
            request_id="opening-recount-pointer-retry",
        )
    assert pointer.value.code == "opening_recount_source_graph_invalid"

    world.db.rollback()
    task = world.db.get(FormalStocktakeTask, task.id)
    task.current_round_no = 2
    task.status = "counting"
    freeze = world.db.scalar(
        select(InventoryFreeze).where(InventoryFreeze.task_id == task.id)
    )
    assert freeze is not None
    freeze.status = "released"
    freeze.valid_to = NOW + timedelta(hours=4)
    freeze.released_by_user_id = world.admin.user.id
    freeze.release_reason = "tamper"
    world.db.commit()
    with pytest.raises(OpeningStocktakeRecountError) as frozen:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=command,
            idempotency_key=key,
            request_id="opening-recount-freeze-retry",
        )
    assert frozen.value.code == "opening_recount_freeze_graph_invalid"

    world.db.rollback()
    freeze = world.db.get(InventoryFreeze, freeze.id)
    freeze.status = "active"
    freeze.valid_to = None
    freeze.released_by_user_id = None
    freeze.release_reason = ""
    state = world.db.scalar(
        select(StateTransitionEvent).where(
            StateTransitionEvent.reason == "opening_recount_opened",
            StateTransitionEvent.aggregate_id == str(task.id),
        )
    )
    assert state is not None
    state.metadata_jsonb = {**state.metadata_jsonb, "scope_count": 999}
    world.db.commit()
    with pytest.raises(OpeningStocktakeRecountError) as effect:
        open_opening_stocktake_recount(
            world.db,
            actor=world.principals["manager_x"],
            command=command,
            idempotency_key=key,
            request_id="opening-recount-effect-retry",
        )
    assert effect.value.code == "opening_recount_idempotency_record_invalid"
    assert world.db.get(StocktakeRound, result.next_round_id) is not None


def test_scope_manifest_order_matches_inventory_posting_and_lock_contract_is_static(world):
    task, _source_round, scope = _prepare_recount_required(world)
    freeze = world.db.scalar(
        select(InventoryFreeze).where(InventoryFreeze.stocktake_scope_id == scope.id)
    )
    assert freeze is not None
    expected = canonical_opening_scope_manifest_sha256(
        task, (scope,), {scope.id: freeze}
    )
    assert recount_service.canonical_opening_recount_scope_manifest_sha256(
        task.region_org_id, (scope,), (freeze,)
    ) == expected

    # Deliberately make scope_no order disagree with owner/location order.
    # Both helpers must still emit the exact same same-schema digest.
    region_id = uuid.UUID("10000000-0000-4000-8000-000000000001")
    high_owner = uuid.UUID("f0000000-0000-4000-8000-000000000001")
    low_owner = uuid.UUID("00000000-0000-4000-8000-000000000001")
    high_scope = SimpleNamespace(
        id=uuid.UUID("f1000000-0000-4000-8000-000000000001"),
        scope_no=1,
        scope_mode="location_all",
        owner_org_id=high_owner,
        location_id=uuid.UUID("f2000000-0000-4000-8000-000000000001"),
        custodian_person_id_snapshot=None,
        assignee_user_id="high-owner-user",
        scope_key="high-owner-scope",
        scope_sha256="a" * 64,
    )
    low_scope = SimpleNamespace(
        id=uuid.UUID("01000000-0000-4000-8000-000000000001"),
        scope_no=2,
        scope_mode="location_all",
        owner_org_id=low_owner,
        location_id=uuid.UUID("02000000-0000-4000-8000-000000000001"),
        custodian_person_id_snapshot=None,
        assignee_user_id="low-owner-user",
        scope_key="low-owner-scope",
        scope_sha256="b" * 64,
    )
    high_freeze = SimpleNamespace(
        stocktake_scope_id=high_scope.id, freeze_mode="hard"
    )
    low_freeze = SimpleNamespace(
        stocktake_scope_id=low_scope.id, freeze_mode="cutoff_replay"
    )
    fake_task = SimpleNamespace(region_org_id=region_id)
    ordered_expected = canonical_opening_scope_manifest_sha256(
        fake_task,
        (high_scope, low_scope),
        {high_scope.id: high_freeze, low_scope.id: low_freeze},
    )
    assert recount_service.canonical_opening_recount_scope_manifest_sha256(
        region_id,
        (high_scope, low_scope),
        (high_freeze, low_freeze),
    ) == ordered_expected

    source = inspect.getsource(recount_service)
    public_replay = inspect.getsource(
        recount_service.validate_opening_recount_round_assignment_evidence
    )
    assert tuple(
        inspect.signature(
            recount_service.validate_opening_recount_round_assignment_evidence
        ).parameters
    ) == ("db", "task_id", "round_id")
    assert public_replay.index(
        "_plan_opening_recount_round_assignment_evidence_from_prelocked_reference_graph"
    ) < public_replay.index(
        "_lock_audit_chain_head_with_proof"
    ) < public_replay.index(
        "_validate_opening_recount_round_assignment_evidence_from_prelocked_task_graph"
    )
    assert "audit_head_prelocked" not in source
    assert "verify_audit_event_in_stream" not in source
    assert '"opening-recount-idempotency"' in source
    assert '"opening-recount-task"' in source
    assert '"opening-recount-source-round"' in source
    assert "pg_advisory_xact_lock" in source
    assert "lock_formal_principal_graph" in source
    assert "_lock_audit_chain_head_with_proof" in source
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert "InventoryTransaction(" not in source
    assert "InventoryMovement(" not in source
