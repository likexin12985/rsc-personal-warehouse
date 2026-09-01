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
import app.formal_services.stocktake_recount_count as recount_count_service
import app.formal_services.stocktake_recount_difference as recount_difference_service
import app.formal_services.stocktake_review as review_service
import app.formal_services.stocktake_task as task_service
from app.formal_services import formal_files
from app.formal_services.stocktake_recount import (
    OpenStocktakeRecountCommand,
    StocktakeRecountScopeAssignmentInput,
    open_stocktake_recount,
)
from app.formal_services.stocktake_recount_count import (
    StocktakePhysicalObservationInput,
    StocktakeRecountCountError,
    StocktakeSnapshotCountInput,
    SubmitStocktakeRecountScopeCountCommand,
    submit_stocktake_recount_scope_count,
)
from app.formal_services.stocktake_recount_difference import (
    GenerateStocktakeRecountDifferenceCommand,
    StocktakeRecountDifferenceError,
    generate_stocktake_recount_differences,
)
from app.foundation_models import AuditEvent, DocumentAttachment, FileObject, OutboxEvent
from app.inventory_models import (
    InventoryMovement,
    InventoryTransaction,
    StockAccount,
    StockBalance,
)
from app.stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    StocktakeCountObservation,
    StocktakeCountSerial,
    StocktakeDifference,
    StocktakeDifferenceSetCompletion,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
)
from app.stocktake_task_schemas import (
    StocktakeScopeSelectionIn,
    StocktakeTaskCreateIn,
)
from test_stocktake_difference_service import (  # noqa: F401
    _append_ledger_movement,
    _evaluate,
    db,
    world,
)
from test_stocktake_review_recount_service import (
    EVALUATED_AT,
    REGION_REVIEWED_AT,
    RECOUNT_OPENED_AT,
    _command,
    _prepare,
    _region,
    review_world,
)
from test_stocktake_task_service import (
    NOW,
    SECRET,
    _create_managed,
    _managed_draft,
    _start,
)


RECOUNT_COUNTED_AT = RECOUNT_OPENED_AT + timedelta(minutes=5)
RECOUNT_EVALUATED_AT = RECOUNT_OPENED_AT + timedelta(minutes=10)
RECOUNT_REVIEWED_AT = RECOUNT_OPENED_AT + timedelta(minutes=15)


@pytest.fixture(autouse=True)
def _recount_clocks(monkeypatch: pytest.MonkeyPatch) -> None:
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
    monkeypatch.setattr(
        recount_count_service, "_database_now", lambda _db: RECOUNT_COUNTED_AT
    )
    monkeypatch.setattr(
        recount_difference_service,
        "_database_now",
        lambda _db: RECOUNT_EVALUATED_AT,
    )


@pytest.fixture
def recount_world(review_world):
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    modules = []
    for name, filename in (
        (
            "stocktake_count_cursor_migration_0033",
            "20260901_0033_stocktake_count_ledger_boundary.py",
        ),
        (
            "stocktake_recount_selected_scope_migration_0034",
            "20260901_0034_stocktake_recount_selected_scope_submission.py",
        ),
    ):
        spec = importlib.util.spec_from_file_location(name, versions / filename)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    cursor_migration, selected_scope_migration = modules
    connection = review_world.db.connection()
    connection.exec_driver_sql(cursor_migration._sqlite_trigger_sql())
    connection.exec_driver_sql(
        selected_scope_migration._sqlite_completion_trigger_sql()
    )
    connection.exec_driver_sql(
        selected_scope_migration._sqlite_round_submission_trigger_sql(
            round_aware=True
        )
    )
    return review_world


def _inventory_write_counts(world) -> tuple[int, int, int, int, int]:
    return (
        world.db.scalar(select(func.count()).select_from(StockAccount)),
        world.db.scalar(select(func.count()).select_from(InventoryTransaction)),
        world.db.scalar(select(func.count()).select_from(InventoryMovement)),
        world.db.scalar(select(func.count()).select_from(StockBalance)),
        world.db.scalar(select(func.count()).select_from(OutboxEvent)),
    )


def _open_recount(review_world, task, source_round, *, key: str):
    _region(
        review_world,
        task,
        source_round,
        _command(
            review_world,
            task,
            source_round,
            decision="recount",
            item_decision="recount",
        ),
        key=f"{key}-review",
    )
    difference = review_world.db.scalar(
        select(StocktakeDifference).where(
            StocktakeDifference.task_id == task.id,
            StocktakeDifference.round_id == source_round.id,
        )
    )
    assert difference is not None and difference.scope_id is not None
    opened = open_stocktake_recount(
        review_world.db,
        actor=review_world.principals["manager_x"],
        command=OpenStocktakeRecountCommand(
            task_id=task.id,
            source_round_id=source_round.id,
            expected_task_version=task.version,
            assignments=(
                StocktakeRecountScopeAssignmentInput(
                    scope_id=difference.scope_id,
                    assignee_user_id=review_world.manager_x.user.id,
                ),
            ),
            reason="按不可变差异执行精确范围复盘",
        ),
        idempotency_key=f"{key}-open",
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}-open",
    )
    round_row = review_world.db.get(StocktakeRound, opened.next_round_id)
    assert round_row is not None
    return difference, opened, round_row


def _count_recount(
    review_world,
    task,
    round_row,
    scope_id,
    *,
    key: str,
    counted_qty: Decimal,
):
    return submit_stocktake_recount_scope_count(
        review_world.db,
        actor=review_world.principals["manager_x"],
        command=SubmitStocktakeRecountScopeCountCommand(
            task_id=task.id,
            round_id=round_row.id,
            scope_id=scope_id,
            count_mode="blind",
            account_counts=(
                StocktakeSnapshotCountInput(
                    stock_account_id=review_world.region_new.id,
                    counted_qty=counted_qty,
                ),
            ),
        ),
        idempotency_key=f"{key}-count",
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}-count",
    )


def _evaluate_recount(review_world, task, round_row, *, key: str):
    return generate_stocktake_recount_differences(
        review_world.db,
        actor=review_world.principals["admin"],
        command=GenerateStocktakeRecountDifferenceCommand(
            task_id=task.id,
            round_id=round_row.id,
            expected_task_version=task.version,
        ),
        idempotency_key=f"{key}-difference",
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}-difference",
    )


def _prepare_two_scope_initial(review_world, *, key: str):
    created = _create_managed(
        review_world,
        key=f"{key}-create",
        draft=StocktakeTaskCreateIn(
            task_type="sample",
            region_org_id=review_world.region_x.id,
            blind_count=True,
            scopes=(
                StocktakeScopeSelectionIn(
                    owner_org_id=review_world.region_x.id,
                    location_id=review_world.region_location.id,
                    assignee_person_id=review_world.manager_x.person.id,
                    scope_mode="filtered",
                    material_id=review_world.material_a.id,
                    condition_code="new",
                    freeze_mode="hard",
                ),
                StocktakeScopeSelectionIn(
                    owner_org_id=review_world.region_x.id,
                    location_id=review_world.region_location.id,
                    assignee_person_id=review_world.manager_x.person.id,
                    scope_mode="filtered",
                    material_id=review_world.material_a.id,
                    condition_code="used",
                    freeze_mode="hard",
                ),
            ),
            deadline=NOW + timedelta(days=2),
            note="两范围局部复盘测试",
        ),
    )
    started = _start(review_world, created.task_id, key=f"{key}-start")
    scopes = tuple(
        review_world.db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == created.task_id)
            .order_by(FormalStocktakeScope.scope_no)
        ).all()
    )
    assert len(scopes) == 2
    scope_by_condition = {row.condition_code: row for row in scopes}
    for condition, account, quantity in (
        ("new", review_world.region_new, Decimal("4.000")),
        ("used", review_world.region_used, Decimal("2.000")),
    ):
        result = count_service.submit_stocktake_initial_scope_count(
            review_world.db,
            actor=review_world.principals["manager_x"],
            command=count_service.SubmitStocktakeInitialScopeCountCommand(
                task_id=created.task_id,
                round_id=started.initial_round_id,
                scope_id=scope_by_condition[condition].id,
                count_mode="blind",
                account_counts=(
                    count_service.StocktakeSnapshotCountInput(
                        stock_account_id=account.id,
                        counted_qty=quantity,
                    ),
                ),
            ),
            idempotency_key=f"{key}-initial-{condition}",
            idempotency_hmac_secret=SECRET,
            trace_request_id=f"trace-{key}-initial-{condition}",
        )
    assert result.round_submitted is True
    task = review_world.db.get(FormalStocktakeTask, created.task_id)
    initial_round = review_world.db.get(StocktakeRound, started.initial_round_id)
    assert task is not None and initial_round is not None
    _evaluate(review_world, task, initial_round, key=f"{key}-evaluate")
    return task, initial_round, scope_by_condition


def test_recount_count_and_difference_are_append_only_and_inventory_free(
    recount_world,
):
    review_world = recount_world
    task, initial_round = _prepare(review_world, key="recount-execution")
    old_difference, opened, recount_round = _open_recount(
        review_world, task, initial_round, key="recount-execution"
    )
    old_snapshot = (
        old_difference.difference_type,
        old_difference.book_qty,
        old_difference.counted_qty,
        old_difference.difference_qty,
        old_difference.reason_code,
    )
    writes_before = _inventory_write_counts(review_world)

    counted = _count_recount(
        review_world,
        task,
        recount_round,
        old_difference.scope_id,
        key="recount-execution",
        counted_qty=Decimal("5.000"),
    )
    assert counted.round_submitted is True
    assert counted.count_ledger_cursor >= task.cutoff_ledger_cursor
    assert task.status == recount_round.status == "submitted"
    assert task.version == opened.task_version + 1

    evaluated = _evaluate_recount(
        review_world, task, recount_round, key="recount-execution"
    )
    assert evaluated.difference_count == 0
    assert evaluated.selected_scope_count == 1
    assert _inventory_write_counts(review_world) == writes_before
    assert review_world.db.get(StocktakeDifference, old_difference.id) is old_difference
    assert old_snapshot == (
        old_difference.difference_type,
        old_difference.book_qty,
        old_difference.counted_qty,
        old_difference.difference_qty,
        old_difference.reason_code,
    )
    assert review_world.db.scalar(
        select(func.count()).select_from(StocktakeDifference).where(
            StocktakeDifference.task_id == task.id,
            StocktakeDifference.round_id == initial_round.id,
        )
    ) == 1
    assert review_world.db.scalar(
        select(func.count()).select_from(StocktakeDifference).where(
            StocktakeDifference.task_id == task.id,
            StocktakeDifference.round_id == recount_round.id,
        )
    ) == 0
    assert review_world.db.scalar(
        select(func.count()).select_from(StocktakeDifferenceSetCompletion).where(
            StocktakeDifferenceSetCompletion.task_id == task.id
        )
    ) == 2
    assert review_world.db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action.in_(
                (
                    "stocktake.recount_scope_count.submitted",
                    "stocktake.recount_round.submitted",
                    "stocktake.recount_difference_set.evaluated",
                )
            )
        )
    ) == 3


def test_recount_count_and_difference_exact_replay_and_tamper_fail_closed(
    recount_world,
):
    review_world = recount_world
    task, initial_round = _prepare(review_world, key="recount-replay")
    difference, _opened, recount_round = _open_recount(
        review_world, task, initial_round, key="recount-replay"
    )
    first = _count_recount(
        review_world,
        task,
        recount_round,
        difference.scope_id,
        key="recount-replay",
        counted_qty=Decimal("4.000"),
    )
    replay = _count_recount(
        review_world,
        task,
        recount_round,
        difference.scope_id,
        key="recount-replay",
        counted_qty=Decimal("4.000"),
    )
    assert replay == replace(first, replayed=True)
    with pytest.raises(StocktakeRecountCountError) as changed:
        _count_recount(
            review_world,
            task,
            recount_round,
            difference.scope_id,
            key="recount-replay",
            counted_qty=Decimal("5.000"),
        )
    assert changed.value.code == "stocktake_recount_count_idempotency_conflict"

    first_difference = _evaluate_recount(
        review_world, task, recount_round, key="recount-replay"
    )
    difference_replay = _evaluate_recount(
        review_world, task, recount_round, key="recount-replay"
    )
    assert difference_replay == replace(first_difference, replayed=True)
    completion = review_world.db.scalar(
        select(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.round_id == recount_round.id
        )
    )
    assert completion is not None
    completion.evidence_manifest_sha256 = "f" * 64
    review_world.db.flush()
    with pytest.raises(StocktakeRecountDifferenceError) as tampered:
        _evaluate_recount(review_world, task, recount_round, key="recount-replay")
    assert tampered.value.code == "stocktake_recount_difference_evidence_invalid"


def test_recount_submission_contains_only_server_sealed_selected_scope(
    recount_world,
):
    review_world = recount_world
    task, initial_round = _prepare(review_world, key="recount-selected")
    difference, _opened, recount_round = _open_recount(
        review_world, task, initial_round, key="recount-selected"
    )
    result = _count_recount(
        review_world,
        task,
        recount_round,
        difference.scope_id,
        key="recount-selected",
        counted_qty=Decimal("5.000"),
    )
    completion = review_world.db.scalar(
        select(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.round_id == recount_round.id
        )
    )
    submission = review_world.db.scalar(
        select(StocktakeRoundSubmission).where(
            StocktakeRoundSubmission.round_id == recount_round.id
        )
    )
    assert completion is not None and submission is not None
    assert completion.scope_id == difference.scope_id
    assert completion.count_ledger_cursor == result.count_ledger_cursor
    assert submission.scope_count == 1
    assert submission.sealing_completion_id == completion.id
    assert recount_round.count_manifest_sha256 == submission.count_manifest_sha256
    assert recount_count_service._as_utc(task.submitted_at) == (
        recount_count_service._as_utc(recount_round.submitted_at)
    )
    assert recount_count_service._as_utc(recount_round.submitted_at) == (
        recount_count_service._as_utc(submission.submitted_at)
    )


def test_partial_scope_recount_seals_without_unselected_task_scope(
    recount_world,
):
    review_world = recount_world
    task, initial_round, scopes = _prepare_two_scope_initial(
        review_world, key="partial-recount"
    )
    difference, _opened, recount_round = _open_recount(
        review_world, task, initial_round, key="partial-recount"
    )
    assert difference.scope_id == scopes["new"].id
    result = _count_recount(
        review_world,
        task,
        recount_round,
        scopes["new"].id,
        key="partial-recount",
        counted_qty=Decimal("5.000"),
    )
    assert result.round_submitted is True
    assert review_world.db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.round_id == recount_round.id,
            StocktakeScopeCountCompletion.scope_id == scopes["used"].id,
        )
    ) == 0
    submission = review_world.db.scalar(
        select(StocktakeRoundSubmission).where(
            StocktakeRoundSubmission.round_id == recount_round.id
        )
    )
    assert submission is not None and submission.scope_count == 1
    evaluated = _evaluate_recount(
        review_world, task, recount_round, key="partial-recount"
    )
    assert evaluated.difference_count == 0


def test_two_consecutive_recount_rounds_reprove_history_without_mutation(
    recount_world,
    monkeypatch,
):
    review_world = recount_world
    task, initial_round = _prepare(review_world, key="multi-recount")
    initial_difference, _opened2, round2 = _open_recount(
        review_world, task, initial_round, key="multi-recount-r2"
    )
    _count_recount(
        review_world,
        task,
        round2,
        initial_difference.scope_id,
        key="multi-recount-r2",
        counted_qty=Decimal("4.000"),
    )
    evaluated2 = _evaluate_recount(
        review_world, task, round2, key="multi-recount-r2"
    )
    assert evaluated2.difference_count == 1
    round2_difference = review_world.db.scalar(
        select(StocktakeDifference).where(
            StocktakeDifference.round_id == round2.id
        )
    )
    assert round2_difference is not None
    immutable_values = {
        initial_difference.id: (
            initial_difference.round_id,
            initial_difference.difference_qty,
            initial_difference.reason_code,
        ),
        round2_difference.id: (
            round2_difference.round_id,
            round2_difference.difference_qty,
            round2_difference.reason_code,
        ),
    }

    monkeypatch.setattr(
        review_service, "_database_now", lambda _db: RECOUNT_REVIEWED_AT
    )
    monkeypatch.setattr(
        recount_service,
        "_database_now",
        lambda _db: RECOUNT_REVIEWED_AT + timedelta(minutes=5),
    )
    _region(
        review_world,
        task,
        round2,
        _command(
            review_world,
            task,
            round2,
            decision="recount",
            item_decision="recount",
        ),
        key="multi-recount-r3-review",
    )
    opened3 = open_stocktake_recount(
        review_world.db,
        actor=review_world.principals["manager_x"],
        command=OpenStocktakeRecountCommand(
            task_id=task.id,
            source_round_id=round2.id,
            expected_task_version=task.version,
            assignments=(
                StocktakeRecountScopeAssignmentInput(
                    scope_id=round2_difference.scope_id,
                    assignee_user_id=review_world.manager_x.user.id,
                ),
            ),
            reason="第二轮仍有差异，继续精确范围复盘",
        ),
        idempotency_key="multi-recount-r3-open",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-multi-recount-r3-open",
    )
    round3 = review_world.db.get(StocktakeRound, opened3.next_round_id)
    assert round3 is not None and round3.round_no == 3
    monkeypatch.setattr(
        recount_count_service,
        "_database_now",
        lambda _db: RECOUNT_REVIEWED_AT + timedelta(minutes=10),
    )
    _count_recount(
        review_world,
        task,
        round3,
        round2_difference.scope_id,
        key="multi-recount-r3",
        counted_qty=Decimal("5.000"),
    )
    monkeypatch.setattr(
        recount_difference_service,
        "_database_now",
        lambda _db: RECOUNT_REVIEWED_AT + timedelta(minutes=15),
    )
    evaluated3 = _evaluate_recount(
        review_world, task, round3, key="multi-recount-r3"
    )
    assert evaluated3.difference_count == 0
    assert task.current_round_no == 3
    for difference_id, expected in immutable_values.items():
        row = review_world.db.get(StocktakeDifference, difference_id)
        assert row is not None
        assert (row.round_id, row.difference_qty, row.reason_code) == expected


def test_cutoff_replay_uses_selected_scope_count_cursor(recount_world):
    review_world = recount_world
    task, initial_round = _prepare(
        review_world,
        key="recount-cutoff-replay",
        draft=_managed_draft(
            review_world,
            material_id=review_world.material_a.id,
            condition_code="new",
            freeze_mode="cutoff_replay",
        ),
    )
    difference, _opened, recount_round = _open_recount(
        review_world, task, initial_round, key="recount-cutoff-replay"
    )
    movement_cursor = _append_ledger_movement(
        review_world,
        from_account=review_world.region_new,
        to_account=review_world.personal_account,
        quantity=Decimal("1.000"),
    )
    counted = _count_recount(
        review_world,
        task,
        recount_round,
        difference.scope_id,
        key="recount-cutoff-replay",
        counted_qty=Decimal("4.000"),
    )
    assert counted.count_ledger_cursor == movement_cursor
    evaluated = _evaluate_recount(
        review_world, task, recount_round, key="recount-cutoff-replay"
    )
    assert evaluated.difference_count == 0


def test_hard_freeze_recount_fails_closed_on_post_cutoff_movement(recount_world):
    review_world = recount_world
    task, initial_round = _prepare(review_world, key="recount-hard-movement")
    difference, _opened, recount_round = _open_recount(
        review_world, task, initial_round, key="recount-hard-movement"
    )
    _append_ledger_movement(
        review_world,
        from_account=review_world.region_new,
        to_account=review_world.personal_account,
        quantity=Decimal("1.000"),
    )
    _count_recount(
        review_world,
        task,
        recount_round,
        difference.scope_id,
        key="recount-hard-movement",
        counted_qty=Decimal("4.000"),
    )
    with pytest.raises(StocktakeRecountDifferenceError) as failure:
        _evaluate_recount(
            review_world, task, recount_round, key="recount-hard-movement"
        )
    assert failure.value.code == "stocktake_recount_difference_reference_graph_invalid"
    assert review_world.db.scalar(
        select(func.count()).select_from(StocktakeDifferenceSetCompletion).where(
            StocktakeDifferenceSetCompletion.round_id == recount_round.id
        )
    ) == 0


def test_cutoff_replay_includes_new_post_cutoff_account(recount_world):
    review_world = recount_world
    task, initial_round = _prepare(
        review_world,
        key="recount-new-account",
        draft=_managed_draft(
            review_world,
            material_id=review_world.material_a.id,
            condition_code="new",
            freeze_mode="cutoff_replay",
        ),
    )
    difference, _opened, recount_round = _open_recount(
        review_world, task, initial_round, key="recount-new-account"
    )
    new_account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=review_world.region_x.id,
        custodian_person_id=None,
        location_id=review_world.region_location.id,
        material_id=review_world.material_a.id,
        condition_code="new",
        availability_bucket="reserved",
        lot_id=None,
        created_at=RECOUNT_OPENED_AT + timedelta(minutes=1),
        updated_at=RECOUNT_OPENED_AT + timedelta(minutes=1),
    )
    review_world.db.add(new_account)
    review_world.db.flush()
    review_world.db.add(
        StockBalance(
            stock_account_id=new_account.id,
            quantity=Decimal("0.000"),
            ledger_cursor=task.cutoff_ledger_cursor,
            version=1,
            created_at=RECOUNT_OPENED_AT + timedelta(minutes=1),
            updated_at=RECOUNT_OPENED_AT + timedelta(minutes=1),
        )
    )
    review_world.db.flush()
    movement_cursor = _append_ledger_movement(
        review_world,
        from_account=review_world.region_new,
        to_account=new_account,
        quantity=Decimal("1.000"),
    )
    counted = submit_stocktake_recount_scope_count(
        review_world.db,
        actor=review_world.principals["manager_x"],
        command=SubmitStocktakeRecountScopeCountCommand(
            task_id=task.id,
            round_id=recount_round.id,
            scope_id=difference.scope_id,
            count_mode="blind",
            account_counts=(
                StocktakeSnapshotCountInput(
                    stock_account_id=review_world.region_new.id,
                    counted_qty=Decimal("4.000"),
                ),
            ),
            physical_observations=(
                StocktakePhysicalObservationInput(
                    material_id=review_world.material_a.id,
                    material_identifier_raw=review_world.material_a.sku_code,
                    material_identifier_type="sku_code",
                    condition_code="new",
                    availability_bucket="reserved",
                    counted_qty=Decimal("1.000"),
                    count_method="manual",
                ),
            ),
        ),
        idempotency_key="recount-new-account-count",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-recount-new-account-count",
    )
    assert counted.count_ledger_cursor == movement_cursor
    evaluated = _evaluate_recount(
        review_world, task, recount_round, key="recount-new-account"
    )
    assert evaluated.difference_count == 0


def test_exact_assignee_and_current_authorization_are_required(recount_world):
    review_world = recount_world
    task, initial_round = _prepare(review_world, key="recount-auth")
    difference, _opened, recount_round = _open_recount(
        review_world, task, initial_round, key="recount-auth"
    )
    command = SubmitStocktakeRecountScopeCountCommand(
        task_id=task.id,
        round_id=recount_round.id,
        scope_id=difference.scope_id,
        count_mode="blind",
        account_counts=(
            StocktakeSnapshotCountInput(
                stock_account_id=review_world.region_new.id,
                counted_qty=Decimal("5.000"),
            ),
        ),
    )
    with pytest.raises(StocktakeRecountCountError) as wrong_actor:
        submit_stocktake_recount_scope_count(
            review_world.db,
            actor=review_world.principals["manager_y"],
            command=command,
            idempotency_key="recount-auth-wrong",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-recount-auth-wrong",
        )
    assert wrong_actor.value.code == "stocktake_recount_count_not_assignee"

    review_world.manager_x.user.authorization_version += 1
    review_world.db.flush()
    with pytest.raises(StocktakeRecountCountError) as stale:
        submit_stocktake_recount_scope_count(
            review_world.db,
            actor=review_world.principals["manager_x"],
            command=command,
            idempotency_key="recount-auth-stale",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-recount-auth-stale",
        )
    assert stale.value.code == "stocktake_recount_count_actor_principal_stale"
    assert review_world.db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.round_id == recount_round.id
        )
    ) == 0


def test_pending_recount_observation_remains_pending_without_account_creation(
    recount_world,
):
    review_world = recount_world
    pending = StocktakePhysicalObservationInput(
        material_id=None,
        material_identifier_raw="UNREADABLE-RECOUNT-MATERIAL",
        material_identifier_type="unknown",
        condition_code="damaged",
        availability_bucket="available",
        counted_qty=Decimal("1.000"),
        serial_no_raw=None,
        serial_identifier_type=None,
        count_method="manual",
    )
    task, initial_round = _prepare(
        review_world,
        key="recount-pending",
        counted_qty=None,
        draft=_managed_draft(review_world, condition_code="damaged"),
        observations=(pending,),
    )
    difference, _opened, recount_round = _open_recount(
        review_world, task, initial_round, key="recount-pending"
    )
    account_count = review_world.db.scalar(
        select(func.count()).select_from(StockAccount)
    )
    counted = submit_stocktake_recount_scope_count(
        review_world.db,
        actor=review_world.principals["manager_x"],
        command=SubmitStocktakeRecountScopeCountCommand(
            task_id=task.id,
            round_id=recount_round.id,
            scope_id=difference.scope_id,
            count_mode="blind",
            physical_observations=(pending,),
        ),
        idempotency_key="recount-pending-count",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-recount-pending-count",
    )
    assert counted.round_submitted is True
    evaluated = _evaluate_recount(
        review_world, task, recount_round, key="recount-pending"
    )
    assert evaluated.pending_observation_difference_count == 1
    observation = review_world.db.scalar(
        select(StocktakeCountObservation).where(
            StocktakeCountObservation.round_id == recount_round.id
        )
    )
    new_difference = review_world.db.scalar(
        select(StocktakeDifference).where(
            StocktakeDifference.round_id == recount_round.id
        )
    )
    assert observation is not None and new_difference is not None
    assert observation.verification_status == "pending_verification"
    assert new_difference.reason_code == "stocktake_pending_verification"
    assert review_world.db.scalar(
        select(func.count()).select_from(StockAccount)
    ) == account_count


def test_open_mode_serial_and_attachment_contracts_are_reused(
    recount_world,
):
    review_world = recount_world
    draft = _managed_draft(
        review_world, material_id=review_world.material_b.id
    ).model_copy(update={"blind_count": False})
    created = _create_managed(
        review_world, key="recount-open-serial-create", draft=draft
    )
    started = _start(
        review_world, created.task_id, key="recount-open-serial-start"
    )
    scope = review_world.db.scalar(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == created.task_id
        )
    )
    assert scope is not None
    initial = count_service.submit_stocktake_initial_scope_count(
        review_world.db,
        actor=review_world.principals["manager_x"],
        command=count_service.SubmitStocktakeInitialScopeCountCommand(
            task_id=created.task_id,
            round_id=started.initial_round_id,
            scope_id=scope.id,
            count_mode="open",
            account_counts=(
                count_service.StocktakeSnapshotCountInput(
                    stock_account_id=review_world.region_serial.id,
                    counted_qty=Decimal("0"),
                    count_method="scan",
                    serial_ids=(),
                    book_qty_confirmation=Decimal("1.000"),
                ),
            ),
        ),
        idempotency_key="recount-open-serial-initial",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-recount-open-serial-initial",
    )
    assert initial.round_submitted is True
    task = review_world.db.get(FormalStocktakeTask, created.task_id)
    initial_round = review_world.db.get(StocktakeRound, started.initial_round_id)
    assert task is not None and initial_round is not None
    _evaluate(
        review_world, task, initial_round, key="recount-open-serial-evaluate"
    )
    difference, _opened, recount_round = _open_recount(
        review_world, task, initial_round, key="recount-open-serial"
    )
    with pytest.raises(StocktakeRecountCountError) as wrong_mode:
        submit_stocktake_recount_scope_count(
            review_world.db,
            actor=review_world.principals["manager_x"],
            command=SubmitStocktakeRecountScopeCountCommand(
                task_id=task.id,
                round_id=recount_round.id,
                scope_id=difference.scope_id,
                count_mode="blind",
                account_counts=(
                    StocktakeSnapshotCountInput(
                        stock_account_id=review_world.region_serial.id,
                        counted_qty=Decimal("1"),
                        count_method="scan",
                        serial_ids=(review_world.serial.id,),
                    ),
                ),
            ),
            idempotency_key="recount-open-serial-wrong-mode",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-recount-open-serial-wrong-mode",
        )
    assert wrong_mode.value.code == "stocktake_recount_count_mode_task_mismatch"

    file_id = uuid.uuid4()
    storage_key = (
        "formal-files/v1/stocktake_evidence/"
        f"{file_id.hex[:2]}/{file_id.hex}"
    )
    uploader = review_world.principals["manager_x"]
    file_row = FileObject(
        id=file_id,
        storage_key=storage_key,
        sha256="a" * 64,
        size_bytes=128,
        mime_type="image/jpeg",
        original_filename="复盘现场.jpg",
        uploaded_by=review_world.principals["manager_x"].user_id,
        status="available",
        metadata_jsonb={
            "authorization_version": uploader.authorization_version,
            "file_id": str(file_id),
            "idempotency_key_hash": "b" * 64,
            "provider": "test_formal_storage",
            "purpose": "stocktake_evidence",
            "request_sha256": formal_files._upload_request_hash(
                formal_files._PreparedUpload(
                    purpose="stocktake_evidence",
                    original_filename="复盘现场.jpg",
                    size_bytes=128,
                    mime_type="image/jpeg",
                    sha256="a" * 64,
                )
            ),
            "schema": "cloud_oam.formal_file_upload_intent.v1",
            "storage_key": storage_key,
            "uploader_person_id": str(uploader.person_id),
            "uploader_user_id": uploader.user_id,
            "completion": {
                "etag_sha256": "d" * 64,
                "head_manifest_sha256": "e" * 64,
                "verified_at": RECOUNT_COUNTED_AT.isoformat(),
            },
        },
        created_at=RECOUNT_COUNTED_AT,
    )
    review_world.db.add(file_row)
    review_world.db.flush()
    result = submit_stocktake_recount_scope_count(
        review_world.db,
        actor=review_world.principals["manager_x"],
        command=SubmitStocktakeRecountScopeCountCommand(
            task_id=task.id,
            round_id=recount_round.id,
            scope_id=difference.scope_id,
            count_mode="open",
            account_counts=(
                StocktakeSnapshotCountInput(
                    stock_account_id=review_world.region_serial.id,
                    counted_qty=Decimal("1"),
                    count_method="scan",
                    serial_ids=(review_world.serial.id,),
                    book_qty_confirmation=Decimal("1.000"),
                ),
            ),
            evidence_file_ids=(file_row.id,),
        ),
        idempotency_key="recount-open-serial-count",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-recount-open-serial-count",
    )
    assert result.evidence_file_count == 1 and result.round_submitted is True
    assert review_world.db.scalar(
        select(func.count()).select_from(StocktakeCountSerial).where(
            StocktakeCountSerial.round_id == recount_round.id
        )
    ) == 1
    assert review_world.db.scalar(
        select(func.count()).select_from(DocumentAttachment).where(
            DocumentAttachment.document_type
            == "stocktake_scope_count_completion",
            DocumentAttachment.file_id == file_row.id,
        )
    ) == 1
    evaluated = _evaluate_recount(
        review_world, task, recount_round, key="recount-open-serial"
    )
    assert evaluated.difference_count == 0
