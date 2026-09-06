from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import importlib.util
from pathlib import Path
import uuid

import pytest
from sqlalchemy import func, select

import app.formal_services.stocktake_count as count_service
import app.formal_services.stocktake_difference as difference_service
import app.formal_services.stocktake_recount as recount_service
import app.formal_services.stocktake_review as review_service
import app.formal_services.stocktake_review_command_status as review_status_service
import app.formal_services.stocktake_task as task_service
from app.formal_access import load_formal_principal
from app.formal_services.stocktake_count import (
    StocktakePhysicalObservationInput,
    StocktakeSnapshotCountInput,
    SubmitStocktakeInitialScopeCountCommand,
    submit_stocktake_initial_scope_count,
)
from app.formal_services.stocktake_recount import (
    OpenStocktakeRecountCommand,
    StocktakeRecountError,
    StocktakeRecountScopeAssignmentInput,
    open_stocktake_recount,
)
from app.formal_services.stocktake_review import (
    StocktakeReviewError,
    StocktakeReviewItemInput,
    SubmitStocktakeReviewCommand,
    submit_stocktake_headquarters_review,
    submit_stocktake_region_review,
)
from app.formal_services.stocktake_task import (
    create_personal_stocktake_draft,
    start_stocktake_task,
)
from app.foundation_models import (
    AuditEvent,
    OutboxEvent,
    Permission,
    Role,
    RoleAssignment,
    RolePermission,
)
from app.inventory_models import (
    InventoryMovement,
    InventoryTransaction,
    StockAccount,
    StockBalance,
)
from app.stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    StocktakeDifference,
    StocktakeRecountCase,
    StocktakeRecountScopeAssignment,
    StocktakeReview,
    StocktakeReviewItem,
    StocktakeRound,
    StocktakeScopeCountCompletion,
)
from app.stocktake_task_schemas import (
    PersonalStocktakeCreateIn,
    StocktakeTaskStartIn,
)
from test_stocktake_difference_service import (  # noqa: F401
    _evaluate,
    _submitted,
    db,
    world,
)
from test_stocktake_task_service import (
    NOW,
    SECRET,
    _managed_draft,
    _termination_draft,
)


EVALUATED_AT = NOW + timedelta(minutes=5)
REGION_REVIEWED_AT = NOW + timedelta(minutes=10)
HQ_REVIEWED_AT = NOW + timedelta(minutes=15)
RECOUNT_OPENED_AT = NOW + timedelta(minutes=20)


@pytest.fixture(autouse=True)
def _fixed_clocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_service, "_database_now", lambda _db: NOW)
    monkeypatch.setattr(count_service, "_database_now", lambda _db: NOW)
    monkeypatch.setattr(
        difference_service, "_database_now", lambda _db: EVALUATED_AT
    )
    monkeypatch.setattr(
        review_service, "_database_now", lambda _db: REGION_REVIEWED_AT
    )
    monkeypatch.setattr(
        recount_service, "_database_now", lambda _db: RECOUNT_OPENED_AT
    )


@pytest.fixture
def review_world(world):
    permissions = {
        action: Permission(
            id=uuid.uuid4(),
            resource="stocktake",
            action=action,
            field_code="",
            description=action,
        )
        for action in ("review_region", "review_headquarters")
    }
    world.db.add_all(permissions.values())
    world.db.flush()
    roles = {
        row.code: row
        for row in world.db.scalars(
            select(Role).where(Role.code.in_(("admin", "provincial_manager")))
        ).all()
    }
    world.db.add_all(
        [
            RolePermission(
                role_id=roles["provincial_manager"].id,
                permission_id=permissions["review_region"].id,
                effect="allow",
            ),
            RolePermission(
                role_id=roles["admin"].id,
                permission_id=permissions["review_headquarters"].id,
                effect="allow",
            ),
        ]
    )
    world.db.flush()
    world.principals.update(
        {
            key: load_formal_principal(
                world.db, principal.user_id, now=REGION_REVIEWED_AT
            )
            for key, principal in tuple(world.principals.items())
        }
    )
    # Run the service suite with the real 0032 SQLite guard bodies as well as
    # ORM constraints.  The full Alembic-chain smoke test separately proves
    # these statements install on a migrated database.
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "20260901_0032_nonopening_stocktake_review_recount.py"
    )
    spec = importlib.util.spec_from_file_location(
        "stocktake_review_recount_migration_0032", migration_path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    connection = world.db.connection()
    for sql in (
        migration._sqlite_review_task_trigger_sql(),
        migration._sqlite_case_trigger_sql(opening_only=False),
        migration._sqlite_task_trigger_sql(opening_only=False),
        migration._sqlite_round_insert_sql(opening_only=False),
        migration._sqlite_round_update_sql(opening_only=False),
    ):
        connection.exec_driver_sql(sql)
    return world


def _prepare(review_world, *, key: str, counted_qty=Decimal("4.000"), **kwargs):
    task, round_row = _submitted(
        review_world,
        key=key,
        counted_qty=counted_qty,
        **kwargs,
    )
    _evaluate(review_world, task, round_row, key=f"{key}-evaluate")
    return task, round_row


def _command(
    world,
    task,
    round_row,
    *,
    decision: str,
    item_decision: str,
    expected_version: int | None = None,
    comment: str | None = None,
):
    differences = tuple(
        world.db.scalars(
            select(StocktakeDifference)
            .where(
                StocktakeDifference.task_id == task.id,
                StocktakeDifference.round_id == round_row.id,
            )
            .order_by(StocktakeDifference.difference_no)
        ).all()
    )
    return SubmitStocktakeReviewCommand(
        task_id=task.id,
        round_id=round_row.id,
        expected_task_version=(
            task.version if expected_version is None else expected_version
        ),
        decision=decision,
        comment=(comment if comment is not None else ("" if decision == "approve" else "需要复盘")),
        items=tuple(
            StocktakeReviewItemInput(
                difference_id=row.id,
                decision=item_decision,
                comment=f"差异 {row.difference_no} 独立复核说明",
            )
            for row in differences
        ),
    )


def _region(world, task, round_row, command, *, key="region-review"):
    return submit_stocktake_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
    )


def _hq(world, task, round_row, command, monkeypatch, *, key="hq-review"):
    monkeypatch.setattr(
        review_service, "_database_now", lambda _db: HQ_REVIEWED_AT
    )
    return submit_stocktake_headquarters_review(
        world.db,
        actor=world.principals["admin"],
        command=command,
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
    )


def _review_status(world, task, round_row, *, stage, actor, trace_request_id):
    read_permission = world.db.scalar(
        select(Permission).where(
            Permission.resource == "stocktake",
            Permission.action == "read",
            Permission.field_code == "",
        )
    )
    if read_permission is None:
        read_permission = Permission(
            id=uuid.uuid4(),
            resource="stocktake",
            action="read",
            field_code="",
            description="read",
        )
        world.db.add(read_permission)
        world.db.flush()
    role_code = "admin" if stage == "headquarters" else "provincial_manager"
    role = world.db.scalar(select(Role).where(Role.code == role_code))
    assert role is not None
    grant = world.db.scalar(
        select(RolePermission).where(
            RolePermission.role_id == role.id,
            RolePermission.permission_id == read_permission.id,
        )
    )
    if grant is None:
        world.db.add(
            RolePermission(
                role_id=role.id,
                permission_id=read_permission.id,
                effect="allow",
            )
        )
        world.db.flush()
    return review_status_service.stocktake_review_command_status(
        world.db,
        actor=actor,
        task_id=task.id,
        round_id=round_row.id,
        review_stage=stage,
        actor_person_id=actor.person_id,
        actor_authorization_version=actor.authorization_version,
        trace_request_id=trace_request_id,
    )


def test_review_command_status_reconstructs_region_fact_without_replay(
    review_world, monkeypatch
):
    task, round_row = _prepare(review_world, key="review-status-region")
    command = _command(
        review_world,
        task,
        round_row,
        decision="approve",
        item_decision="accept_for_posting",
    )
    result = _region(
        review_world,
        task,
        round_row,
        command,
        key="review-status-region-write",
    )
    review_world.db.commit()
    before_review_count = review_world.db.scalar(
        select(func.count()).select_from(StocktakeReview)
    )
    status = _review_status(
        review_world,
        task,
        round_row,
        stage="region",
        actor=review_world.principals["manager_x"],
        trace_request_id="trace-review-status-region-write",
    )
    assert status.lookup_status == "confirmed"
    assert status.command is not None
    assert status.command.review_id == result.review_id
    assert status.command.resulting_task_version == result.resulting_task_version
    assert review_world.db.scalar(
        select(func.count()).select_from(StocktakeReview)
    ) == before_review_count

    unseen = _review_status(
        review_world,
        task,
        round_row,
        stage="region",
        actor=review_world.principals["manager_x"],
        trace_request_id="trace-review-status-region-never-seen",
    )
    assert unseen.lookup_status == "not_observed"
    assert unseen.command is None


def test_review_command_status_reconstructs_headquarters_approval(
    review_world, monkeypatch
):
    task, round_row = _prepare(review_world, key="review-status-hq")
    _region(
        review_world,
        task,
        round_row,
        _command(
            review_world,
            task,
            round_row,
            decision="approve",
            item_decision="accept_for_posting",
        ),
        key="review-status-hq-region",
    )
    hq = _hq(
        review_world,
        task,
        round_row,
        _command(
            review_world,
            task,
            round_row,
            decision="approve",
            item_decision="accept_for_posting",
        ),
        monkeypatch,
        key="review-status-hq-write",
    )
    review_world.db.commit()
    status = _review_status(
        review_world,
        task,
        round_row,
        stage="headquarters",
        actor=review_world.principals["admin"],
        trace_request_id="trace-review-status-hq-write",
    )
    assert status.lookup_status == "confirmed"
    assert status.command is not None
    assert status.command.review_id == hq.review_id
    assert status.command.resulting_task_status == "approved"
    assert status.command.ready_for_posting is True


def _write_counts(world):
    return (
        world.db.scalar(select(func.count()).select_from(StockAccount)),
        world.db.scalar(select(func.count()).select_from(InventoryTransaction)),
        world.db.scalar(select(func.count()).select_from(InventoryMovement)),
        world.db.scalar(select(func.count()).select_from(StockBalance)),
        world.db.scalar(select(func.count()).select_from(OutboxEvent)),
    )


def test_region_then_headquarters_approve_are_independent_and_inventory_free(
    review_world, monkeypatch
):
    task, round_row = _prepare(review_world, key="approve-chain")
    writes_before = _write_counts(review_world)
    region = _region(
        review_world,
        task,
        round_row,
        _command(
            review_world,
            task,
            round_row,
            decision="approve",
            item_decision="accept_for_posting",
        ),
        key="approve-chain-region",
    )
    assert region.resulting_task_status == "hq_review"
    assert region.resulting_task_version == region.expected_task_version + 1
    assert region.task_version == region.resulting_task_version
    assert region.ready_for_posting is False
    assert task.status == "hq_review"
    hq = _hq(
        review_world,
        task,
        round_row,
        _command(
            review_world,
            task,
            round_row,
            decision="approve",
            item_decision="accept_for_posting",
        ),
        monkeypatch,
        key="approve-chain-hq",
    )

    reviews = tuple(
        review_world.db.scalars(
            select(StocktakeReview).order_by(StocktakeReview.reviewed_at)
        ).all()
    )
    assert [row.review_stage for row in reviews] == ["region", "headquarters"]
    assert reviews[0].reviewer_person_id != reviews[1].reviewer_person_id
    assert (
        reviews[0].expected_task_version,
        reviews[0].resulting_task_version,
        reviews[1].expected_task_version,
        reviews[1].resulting_task_version,
    ) == (
        region.expected_task_version,
        region.resulting_task_version,
        hq.expected_task_version,
        hq.resulting_task_version,
    )
    assert region.review_id != hq.review_id
    assert hq.resulting_task_status == "approved"
    assert hq.expected_task_version == region.resulting_task_version
    assert hq.resulting_task_version == hq.expected_task_version + 1
    assert hq.task_version == hq.resulting_task_version
    assert hq.ready_for_posting is True
    assert task.status == "approved"
    assert _write_counts(review_world) == writes_before
    assert review_world.db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action.in_(
                (
                    "stocktake.nonopening.region_reviewed",
                    "stocktake.nonopening.headquarters_reviewed",
                )
            )
        )
    ) == 2


def test_zero_difference_requires_both_reviews(
    review_world, monkeypatch
):
    zero_task, zero_round = _prepare(
        review_world, key="zero-review", counted_qty=Decimal("5.000")
    )
    zero_region = _region(
        review_world,
        zero_task,
        zero_round,
        _command(
            review_world,
            zero_task,
            zero_round,
            decision="approve",
            item_decision="accept_for_posting",
        ),
        key="zero-review-region",
    )
    assert zero_region.item_count == 0
    zero_hq = _hq(
        review_world,
        zero_task,
        zero_round,
        _command(
            review_world,
            zero_task,
            zero_round,
            decision="approve",
            item_decision="accept_for_posting",
        ),
        monkeypatch,
        key="zero-review-hq",
    )
    assert zero_hq.ready_for_posting is True


def test_all_no_adjustment_is_deliberate_and_requires_both_reviews(
    review_world, monkeypatch
):
    task, round_row = _prepare(review_world, key="no-adjustment")
    _region(
        review_world,
        task,
        round_row,
        _command(
            review_world,
            task,
            round_row,
            decision="approve",
            item_decision="no_adjustment",
        ),
        key="no-adjustment-region",
    )
    hq = _hq(
        review_world,
        task,
        round_row,
        _command(
            review_world,
            task,
            round_row,
            decision="approve",
            item_decision="no_adjustment",
        ),
        monkeypatch,
        key="no-adjustment-hq",
    )
    assert hq.ready_for_posting is True


@pytest.mark.parametrize(
    "task_type", ("sample", "ad_hoc", "full", "termination")
)
def test_all_managed_nonopening_types_enter_two_stage_review(
    review_world, monkeypatch, task_type
):
    if task_type == "full":
        draft = _managed_draft(review_world, task_type="full")
        account_counts = (
            StocktakeSnapshotCountInput(
                stock_account_id=review_world.region_new.id,
                counted_qty=Decimal("4.000"),
            ),
            StocktakeSnapshotCountInput(
                stock_account_id=review_world.region_used.id,
                counted_qty=Decimal("2.000"),
            ),
            StocktakeSnapshotCountInput(
                stock_account_id=review_world.region_serial.id,
                counted_qty=Decimal("1"),
                serial_ids=(review_world.serial.id,),
            ),
        )
    elif task_type == "termination":
        draft = _termination_draft(review_world)
        account_counts = (
            StocktakeSnapshotCountInput(
                stock_account_id=review_world.personal_account.id,
                counted_qty=Decimal("2.000"),
            ),
        )
    else:
        draft = _managed_draft(
            review_world,
            task_type=task_type,
            material_id=review_world.material_a.id,
            condition_code="new",
        )
        account_counts = None
    task, round_row = _prepare(
        review_world,
        key=f"type-{task_type}",
        draft=draft,
        account_counts=account_counts,
    )
    region = _region(
        review_world,
        task,
        round_row,
        _command(
            review_world,
            task,
            round_row,
            decision="approve",
            item_decision="accept_for_posting",
        ),
        key=f"type-{task_type}-region",
    )
    assert region.resulting_task_status == "hq_review"
    hq = _hq(
        review_world,
        task,
        round_row,
        _command(
            review_world,
            task,
            round_row,
            decision="approve",
            item_decision="accept_for_posting",
        ),
        monkeypatch,
        key=f"type-{task_type}-hq",
    )
    assert hq.resulting_task_status == "approved"
    assert hq.ready_for_posting is True


def test_personal_stocktake_uses_same_two_stage_review_without_posting(
    review_world, monkeypatch
):
    created = create_personal_stocktake_draft(
        review_world.db,
        actor=review_world.principals["technician"],
        draft=PersonalStocktakeCreateIn(
            blind_count=True,
            freeze_mode="hard",
            note="个人盘点复核测试",
        ),
        idempotency_key="personal-review-create",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-personal-review-create",
    )
    started = start_stocktake_task(
        review_world.db,
        actor=review_world.principals["technician"],
        task_id=created.task_id,
        command=StocktakeTaskStartIn(expected_version=0),
        idempotency_key="personal-review-start",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-personal-review-start",
    )
    task = review_world.db.get(FormalStocktakeTask, created.task_id)
    round_row = review_world.db.get(StocktakeRound, started.initial_round_id)
    scope = review_world.db.scalar(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == created.task_id
        )
    )
    assert task is not None and round_row is not None and scope is not None
    counted = submit_stocktake_initial_scope_count(
        review_world.db,
        actor=review_world.principals["technician"],
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=task.id,
            round_id=round_row.id,
            scope_id=scope.id,
            count_mode="blind",
            account_counts=(
                StocktakeSnapshotCountInput(
                    stock_account_id=review_world.personal_account.id,
                    counted_qty=Decimal("2.000"),
                ),
            ),
        ),
        idempotency_key="personal-review-count",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-personal-review-count",
    )
    assert counted.round_submitted
    _evaluate(
        review_world,
        task,
        round_row,
        key="personal-review-evaluate",
    )
    writes_before = _write_counts(review_world)
    _region(
        review_world,
        task,
        round_row,
        _command(
            review_world,
            task,
            round_row,
            decision="approve",
            item_decision="accept_for_posting",
        ),
        key="personal-review-region",
    )
    hq = _hq(
        review_world,
        task,
        round_row,
        _command(
            review_world,
            task,
            round_row,
            decision="approve",
            item_decision="accept_for_posting",
        ),
        monkeypatch,
        key="personal-review-hq",
    )
    assert hq.resulting_task_status == "approved"
    assert hq.ready_for_posting is True
    assert _write_counts(review_world) == writes_before


def test_pending_unknown_cannot_approve_and_opens_append_only_recount(
    review_world,
):
    task, round_row = _prepare(
        review_world,
        key="pending-recount",
        counted_qty=None,
        # The empty damaged-condition scope has no snapshot accounts, so the
        # unknown observation is both inside the selected scope and the only
        # difference in the sealed set.
        draft=_managed_draft(review_world, condition_code="damaged"),
        observations=(
            StocktakePhysicalObservationInput(
                material_id=None,
                material_identifier_raw="UNREADABLE-PENDING",
                material_identifier_type="unknown",
                    condition_code="damaged",
                availability_bucket="available",
                counted_qty=Decimal("1.000"),
                serial_no_raw=None,
                serial_identifier_type=None,
                count_method="manual",
            ),
        ),
    )
    approve = _command(
        review_world,
        task,
        round_row,
        decision="approve",
        item_decision="accept_for_posting",
    )
    with pytest.raises(StocktakeReviewError) as failure:
        _region(
            review_world,
            task,
            round_row,
            approve,
            key="pending-approve-forbidden",
        )
    assert failure.value.code == "stocktake_review_pending_verification_not_postable"

    region = _region(
        review_world,
        task,
        round_row,
        _command(
            review_world,
            task,
            round_row,
            decision="recount",
            item_decision="pending_verification",
        ),
        key="pending-region-recount",
    )
    assert region.resulting_task_status == "recount_required"
    assert region.ready_for_posting is False
    difference = review_world.db.scalar(select(StocktakeDifference))
    assert difference is not None and difference.scope_id is not None
    writes_before = _write_counts(review_world)
    result = open_stocktake_recount(
        review_world.db,
        actor=review_world.principals["manager_x"],
        command=OpenStocktakeRecountCommand(
            task_id=task.id,
            source_round_id=round_row.id,
            expected_task_version=task.version,
            assignments=(
                StocktakeRecountScopeAssignmentInput(
                    scope_id=difference.scope_id,
                    assignee_user_id=review_world.manager_x.user.id,
                ),
            ),
            reason="待核实实物必须重新盘点",
        ),
        idempotency_key="pending-open-recount",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-pending-open-recount",
    )

    source = review_world.db.get(StocktakeRound, round_row.id)
    successor = review_world.db.get(StocktakeRound, result.next_round_id)
    assert source is not None and source.status == "submitted"
    assert successor is not None
    assert successor.round_no == 2 and successor.round_type == "recount"
    assert successor.status == "counting"
    assert source.recount_case_id is None
    assert result.scope_count == result.assignment_count == 1
    assert task.status == "counting" and task.current_round_no == 2
    assert review_world.db.scalar(
        select(func.count()).select_from(StocktakeRecountCase)
    ) == 1
    assert review_world.db.scalar(
        select(func.count()).select_from(StocktakeRecountScopeAssignment)
    ) == 1
    assert _write_counts(review_world) == writes_before


def test_headquarters_recount_is_terminal_and_can_open_recount(
    review_world, monkeypatch
):
    task, round_row = _prepare(review_world, key="hq-recount")
    _region(
        review_world,
        task,
        round_row,
        _command(
            review_world,
            task,
            round_row,
            decision="approve",
            item_decision="accept_for_posting",
        ),
        key="hq-recount-region",
    )
    hq = _hq(
        review_world,
        task,
        round_row,
        _command(
            review_world,
            task,
            round_row,
            decision="recount",
            item_decision="recount",
        ),
        monkeypatch,
        key="hq-recount-hq",
    )
    assert hq.resulting_task_status == "recount_required"
    difference = review_world.db.scalar(select(StocktakeDifference))
    assert difference is not None and difference.scope_id is not None
    result = open_stocktake_recount(
        review_world.db,
        actor=review_world.principals["admin"],
        command=OpenStocktakeRecountCommand(
            task_id=task.id,
            source_round_id=round_row.id,
            expected_task_version=task.version,
            assignments=(
                StocktakeRecountScopeAssignmentInput(
                    scope_id=difference.scope_id,
                    assignee_user_id=review_world.manager_x.user.id,
                ),
            ),
            reason="总部要求复盘",
        ),
        idempotency_key="hq-open-recount",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-hq-open-recount",
    )
    case = review_world.db.get(StocktakeRecountCase, result.recount_case_id)
    assert case is not None and case.trigger_review_id == hq.review_id


def test_review_replay_and_idempotency_conflict(
    review_world,
):
    task, round_row = _prepare(review_world, key="guards")
    command = _command(
        review_world,
        task,
        round_row,
        decision="approve",
        item_decision="accept_for_posting",
    )
    first = _region(
        review_world, task, round_row, command, key="guards-region"
    )
    replay = _region(
        review_world, task, round_row, command, key="guards-region"
    )
    assert replay == replace(first, replayed=True)
    assert review_world.db.scalar(select(func.count()).select_from(StocktakeReview)) == 1

    changed = replace(
        command,
        items=tuple(
            replace(row, comment="不同的逐项说明") for row in command.items
        ),
    )
    with pytest.raises(StocktakeReviewError) as conflict:
        _region(review_world, task, round_row, changed, key="guards-region")
    assert conflict.value.code == "stocktake_review_idempotency_conflict"


def test_wrong_region_manager_and_stale_version_fail_closed(review_world):
    task, round_row = _prepare(review_world, key="wrong-manager")
    with pytest.raises(StocktakeReviewError) as forbidden:
        submit_stocktake_region_review(
            review_world.db,
            actor=review_world.principals["manager_y"],
            command=_command(
                review_world,
                task,
                round_row,
                decision="approve",
                item_decision="accept_for_posting",
            ),
            idempotency_key="wrong-manager-review",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-wrong-manager-review",
        )
    assert forbidden.value.code == "stocktake_review_forbidden"

    with pytest.raises(StocktakeReviewError) as stale:
        _region(
            review_world,
            task,
            round_row,
            _command(
                review_world,
                task,
                round_row,
                decision="approve",
                item_decision="accept_for_posting",
                expected_version=task.version + 1,
            ),
            key="stale-version-review",
        )
    assert stale.value.code == "stocktake_review_version_conflict"


def test_recount_replay_and_idempotency_conflict(
    review_world,
):
    task, round_row = _prepare(review_world, key="recount-replay")
    _region(
        review_world,
        task,
        round_row,
        _command(
            review_world,
            task,
            round_row,
            decision="recount",
            item_decision="recount",
        ),
        key="recount-replay-region",
    )
    difference = review_world.db.scalar(select(StocktakeDifference))
    assert difference is not None and difference.scope_id is not None
    command = OpenStocktakeRecountCommand(
        task_id=task.id,
        source_round_id=round_row.id,
        expected_task_version=task.version,
        assignments=(
            StocktakeRecountScopeAssignmentInput(
                scope_id=difference.scope_id,
                assignee_user_id=review_world.manager_x.user.id,
            ),
        ),
        reason="区域要求复盘",
    )
    first = open_stocktake_recount(
        review_world.db,
        actor=review_world.principals["manager_x"],
        command=command,
        idempotency_key="recount-replay-open",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-recount-replay-open",
    )
    replay = open_stocktake_recount(
        review_world.db,
        actor=review_world.principals["manager_x"],
        command=command,
        idempotency_key="recount-replay-open",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-recount-replay-open",
    )
    assert replay == replace(first, replayed=True)

    with pytest.raises(StocktakeRecountError) as conflict:
        open_stocktake_recount(
            review_world.db,
            actor=review_world.principals["manager_x"],
            command=replace(command, reason="不同复盘原因"),
            idempotency_key="recount-replay-open",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-recount-replay-open",
        )
    assert conflict.value.code == "stocktake_recount_idempotency_conflict"


def test_current_reviewer_authorization_invalidation_fails_closed(review_world):
    task, round_row = _prepare(review_world, key="revoked-review")
    manager_assignment = review_world.db.scalar(
        select(RoleAssignment)
        .join(Role, Role.id == RoleAssignment.role_id)
        .where(
            RoleAssignment.user_id == review_world.manager_x.user.id,
            Role.code == "provincial_manager",
        )
    )
    assert manager_assignment is not None
    manager_assignment.revoked_at = REGION_REVIEWED_AT - timedelta(seconds=1)
    manager_assignment.revoked_by = review_world.principals["admin"].user_id
    manager_assignment.status = "revoked"
    review_world.db.flush()
    with pytest.raises(StocktakeReviewError) as revoked:
        _region(
            review_world,
            task,
            round_row,
            _command(
                review_world,
                task,
                round_row,
                decision="approve",
                item_decision="accept_for_posting",
            ),
            key="revoked-manager-review",
        )
    assert revoked.value.code == "stocktake_review_actor_not_current"


def test_cutoff_replay_review_recomputes_from_sealed_count_cursor(review_world):
    task, round_row = _prepare(
        review_world,
        key="cutoff-replay-sealed",
        draft=_managed_draft(
            review_world,
            material_id=review_world.material_a.id,
            condition_code="new",
            freeze_mode="cutoff_replay",
        ),
    )
    result = _region(
        review_world,
        task,
        round_row,
        _command(
            review_world,
            task,
            round_row,
            decision="approve",
            item_decision="accept_for_posting",
        ),
        key="cutoff-replay-sealed-review",
    )
    assert result.resulting_task_status == "hq_review"
    assert result.ready_for_posting is False


def test_cutoff_replay_review_fails_closed_for_legacy_null_count_cursor(
    review_world,
):
    task, round_row = _prepare(
        review_world,
        key="cutoff-replay-legacy-unsealed",
        draft=_managed_draft(
            review_world,
            material_id=review_world.material_a.id,
            condition_code="new",
            freeze_mode="cutoff_replay",
        ),
    )
    completion = review_world.db.scalar(
        select(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.task_id == task.id,
            StocktakeScopeCountCompletion.round_id == round_row.id,
        )
    )
    assert completion is not None
    completion.count_ledger_cursor = None
    review_world.db.flush()
    with pytest.raises(StocktakeReviewError) as failure:
        _region(
            review_world,
            task,
            round_row,
            _command(
                review_world,
                task,
                round_row,
                decision="approve",
                item_decision="accept_for_posting",
            ),
            key="cutoff-replay-legacy-unsealed-review",
        )
    assert failure.value.code == "stocktake_review_source_evidence_invalid"
    assert failure.value.category == "service_unavailable"
    assert task.status == "submitted"
    assert review_world.db.scalar(
        select(func.count()).select_from(StocktakeReview)
    ) == 0


def test_historical_source_round_and_difference_tampering_fail_closed(
    review_world,
):
    task, round_row = _prepare(review_world, key="tamper-review")
    difference = review_world.db.scalar(select(StocktakeDifference))
    assert difference is not None
    difference.reason_text = "tampered"
    review_world.db.flush()
    with pytest.raises(StocktakeReviewError) as failure:
        _region(
            review_world,
            task,
            round_row,
            _command(
                review_world,
                task,
                round_row,
                decision="approve",
                item_decision="accept_for_posting",
            ),
            key="tamper-review-region",
        )
    assert failure.value.code == "stocktake_review_source_evidence_invalid"


def test_review_command_status_fails_closed_for_tampered_audit_manifest(
    review_world,
):
    task, round_row = _prepare(review_world, key="tamper-review-status-audit")
    result = _region(
        review_world,
        task,
        round_row,
        _command(
            review_world,
            task,
            round_row,
            decision="approve",
            item_decision="accept_for_posting",
        ),
        key="tamper-review-status-audit-write",
    )
    review_world.db.commit()
    # The audit aggregate id is the review id, while the review row itself is
    # queried separately; locate the immutable event by its action instead of
    # assuming shared primary keys.
    audit = review_world.db.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.aggregate_type == "stocktake_review",
            AuditEvent.aggregate_id == str(result.review_id),
            AuditEvent.action == "stocktake.nonopening.region_reviewed",
        )
    )
    assert audit is not None and isinstance(audit.after_jsonb, dict)
    audit.after_jsonb = {
        **audit.after_jsonb,
        "decision_manifest_sha256": "0" * 64,
    }
    review_world.db.flush()
    with pytest.raises(review_status_service.StocktakeReviewCommandStatusError) as failure:
        _review_status(
            review_world,
            task,
            round_row,
            stage="region",
            actor=review_world.principals["manager_x"],
            trace_request_id="trace-tamper-review-status-audit-write",
        )
    assert failure.value.category == "service_unavailable"
