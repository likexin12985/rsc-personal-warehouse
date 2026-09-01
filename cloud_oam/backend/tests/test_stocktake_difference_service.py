from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
import inspect
import uuid

import pytest
from sqlalchemy import func, select

import app.formal_services.stocktake_count as count_service
import app.formal_services.stocktake_difference as service
import app.formal_services.stocktake_task as task_service
from app.formal_services.stocktake_count import (
    StocktakePhysicalObservationInput,
    StocktakeSnapshotCountInput,
    SubmitStocktakeInitialScopeCountCommand,
    submit_stocktake_initial_scope_count,
)
from app.formal_services.stocktake_difference import (
    GenerateStocktakeDifferenceCommand,
    StocktakeDifferenceError,
    generate_stocktake_initial_differences,
)
from app.formal_services.audit_chain import AuditChainError
from app.foundation_models import AuditEvent, OutboxEvent
from app.inventory_models import (
    InventoryLedgerHead,
    InventoryMovement,
    InventoryMovementSerial,
    InventorySerial,
    InventoryTransaction,
    SerialCurrentPosition,
    StockAccount,
    StockBalance,
)
from app.stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    StocktakeCountLine,
    StocktakeCountObservation,
    StocktakeCountSerial,
    StocktakeDifference,
    StocktakeDifferenceSetCompletion,
    StocktakeRound,
    StocktakeScopeCountCompletion,
    StocktakeSnapshotLine,
)
from app.stocktake_task_schemas import (
    StocktakeScopeSelectionIn,
    StocktakeTaskCreateIn,
)
from test_stocktake_task_service import (  # noqa: F401
    NOW,
    SECRET,
    _create_managed,
    _managed_draft,
    _start,
    db,
    world,
)


EVALUATED_AT = NOW + timedelta(minutes=5)


@pytest.fixture(autouse=True)
def _fixed_clocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_service, "_database_now", lambda _db: NOW)
    monkeypatch.setattr(count_service, "_database_now", lambda _db: NOW)
    monkeypatch.setattr(service, "_database_now", lambda _db: EVALUATED_AT)


def _submitted(
    world,
    *,
    key: str,
    counted_qty: Decimal | None,
    observations=(),
    draft=None,
    account_counts=None,
):
    created = _create_managed(
        world,
        key=f"{key}-create",
        draft=draft
        or _managed_draft(
            world,
            material_id=world.material_a.id,
            condition_code="new",
        ),
    )
    started = _start(world, created.task_id, key=f"{key}-start")
    scope = world.db.scalar(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == created.task_id
        )
    )
    assert scope is not None
    count_result = submit_stocktake_initial_scope_count(
        world.db,
        actor=world.principals["manager_x"],
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=created.task_id,
            round_id=started.initial_round_id,
            scope_id=scope.id,
            count_mode="blind",
            account_counts=(
                tuple(account_counts)
                if account_counts is not None
                else () if counted_qty is None else (
                    StocktakeSnapshotCountInput(
                        stock_account_id=world.region_new.id,
                        counted_qty=counted_qty,
                    ),
                )
            ),
            physical_observations=tuple(observations),
        ),
        idempotency_key=f"{key}-count",
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}-count",
    )
    assert count_result.round_submitted
    task = world.db.get(FormalStocktakeTask, created.task_id)
    round_row = world.db.get(StocktakeRound, started.initial_round_id)
    assert task is not None and round_row is not None
    return task, round_row


def _evaluate(world, task, round_row, *, actor="admin", key="evaluate"):
    return generate_stocktake_initial_differences(
        world.db,
        actor=world.principals[actor],
        command=GenerateStocktakeDifferenceCommand(
            task_id=task.id,
            round_id=round_row.id,
            expected_task_version=task.version,
        ),
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
    )


def _write_counts(world) -> tuple[int, int, int, int]:
    return (
        world.db.scalar(select(func.count()).select_from(InventoryTransaction)),
        world.db.scalar(select(func.count()).select_from(InventoryMovement)),
        world.db.scalar(select(func.count()).select_from(StockBalance)),
        world.db.scalar(select(func.count()).select_from(OutboxEvent)),
    )


def _append_ledger_movement(
    world,
    *,
    from_account: StockAccount | None,
    to_account: StockAccount | None,
    quantity: Decimal,
    serial_ids: tuple[uuid.UUID, ...] = (),
) -> int:
    head = world.db.get(
        InventoryLedgerHead,
        task_service.INVENTORY_LEDGER_HEAD_ID,
    )
    assert head is not None
    cursor = head.next_cursor
    occurred_at = NOW + timedelta(seconds=cursor)
    transaction = InventoryTransaction(
        id=uuid.uuid4(),
        transaction_no=f"TX-REPLAY-{cursor}",
        movement_type=(
            "transfer"
            if from_account is not None and to_account is not None
            else "stocktake_gain" if to_account is not None else "stocktake_loss"
        ),
        source_document_type="cutoff_replay_test",
        source_document_id=str(cursor),
        posting_key=f"cutoff-replay-test:{cursor}",
        idempotency_key_hash=f"{cursor:064x}",
        request_hash=f"{cursor + 100:064x}",
        status="posted",
        effective_at=occurred_at,
        posted_at=occurred_at,
        ledger_cursor=cursor,
        reversed_transaction_id=None,
        actor_user_id=world.principals["admin"].user_id,
        created_at=occurred_at,
    )
    movement = InventoryMovement(
        id=uuid.uuid4(),
        transaction_id=transaction.id,
        line_no=1,
        from_account_id=from_account.id if from_account is not None else None,
        to_account_id=to_account.id if to_account is not None else None,
        external_boundary_code=(
            None
            if from_account is not None and to_account is not None
            else "TEST_EXTERNAL"
        ),
        quantity=quantity,
        created_at=occurred_at,
    )
    world.db.add(transaction)
    world.db.flush()
    world.db.add(movement)
    world.db.flush()
    for serial_id in serial_ids:
        world.db.add(
            InventoryMovementSerial(
                movement_id=movement.id,
                transaction_id=transaction.id,
                serial_id=serial_id,
                created_at=occurred_at,
            )
        )
        position = world.db.get(SerialCurrentPosition, serial_id)
        assert position is not None
        position.stock_account_id = (
            to_account.id if to_account is not None else None
        )
        position.last_movement_id = movement.id
        position.updated_at = occurred_at
    if from_account is not None:
        balance = world.db.get(StockBalance, from_account.id)
        assert balance is not None and balance.quantity >= quantity
        balance.quantity -= quantity
        balance.ledger_cursor = cursor
        balance.version += 1
    if to_account is not None:
        balance = world.db.get(StockBalance, to_account.id)
        assert balance is not None
        balance.quantity += quantity
        balance.ledger_cursor = cursor
        balance.version += 1
    head.next_cursor = cursor + 1
    world.db.flush()
    return cursor


def test_zero_difference_is_sealed_by_real_later_admin_without_inventory_writes(world):
    task, round_row = _submitted(
        world,
        key="zero-difference",
        counted_qty=Decimal("5.000"),
    )
    writes_before = _write_counts(world)

    result = _evaluate(world, task, round_row, key="zero-difference-evaluate")

    completion = world.db.get(StocktakeDifferenceSetCompletion, result.completion_id)
    assert completion is not None
    assert result.difference_status == "evaluated"
    assert result.difference_count == 0
    assert result.total_affected_qty == Decimal("0.000")
    assert result.task_status == task.status == "submitted"
    assert result.round_status == round_row.status == "submitted"
    assert completion.completed_by_user_id == world.principals["admin"].user_id
    assert completion.completed_by_user_id != round_row.submitted_by_user_id
    assert service._as_utc(completion.completed_at) == EVALUATED_AT
    assert service._as_utc(completion.completed_at) > service._as_utc(
        round_row.submitted_at
    )
    assert completion.role_code == "admin"
    assert completion.scope_type == "national"
    assert completion.scope_id_snapshot == "*"
    assert len(completion.authorization_sha256) == 64
    assert world.db.scalar(select(func.count()).select_from(StocktakeDifference)) == 0
    assert _write_counts(world) == writes_before
    assert world.db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == "stocktake.initial_difference_set.evaluated"
        )
    ) == 1


@pytest.mark.parametrize(
    ("counted_qty", "difference_type", "difference_qty", "affected_qty"),
    (
        (Decimal("3.000"), "missing", Decimal("-2.000"), Decimal("2.000")),
        (Decimal("7.000"), "excess", Decimal("2.000"), Decimal("2.000")),
    ),
)
def test_missing_and_excess_are_derived_from_cutoff_snapshot(
    world,
    counted_qty,
    difference_type,
    difference_qty,
    affected_qty,
):
    task, round_row = _submitted(
        world,
        key=f"quantity-{difference_type}",
        counted_qty=counted_qty,
    )
    # Current balance is deliberately changed after cutoff; evaluation must
    # remain bound to the immutable snapshot quantity of 5.
    balance = world.db.get(StockBalance, world.region_new.id)
    assert balance is not None
    balance.quantity = Decimal("99.000")
    world.db.flush()
    writes_before = _write_counts(world)

    result = _evaluate(
        world,
        task,
        round_row,
        key=f"quantity-{difference_type}-evaluate",
    )

    row = world.db.scalar(select(StocktakeDifference))
    assert result.difference_count == 1
    assert row is not None
    assert row.difference_type == difference_type
    assert row.book_qty == (Decimal("2.000") if difference_type == "missing" else Decimal("0.000"))
    assert row.counted_qty == (Decimal("0.000") if difference_type == "missing" else Decimal("2.000"))
    assert row.difference_qty == difference_qty
    assert row.affected_qty == affected_qty
    assert _write_counts(world) == writes_before


def test_exact_replay_and_different_request_conflict(world):
    task, round_row = _submitted(
        world,
        key="replay",
        counted_qty=Decimal("4.000"),
    )
    first = _evaluate(world, task, round_row, key="replay-evaluate")
    replay = _evaluate(world, task, round_row, key="replay-evaluate")
    assert replay.replayed is True
    assert replay.completion_id == first.completion_id
    assert world.db.scalar(select(func.count()).select_from(StocktakeDifference)) == 1
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeDifferenceSetCompletion)
    ) == 1

    with pytest.raises(StocktakeDifferenceError) as failure:
        _evaluate(world, task, round_row, key="different-request")
    assert failure.value.code == "stocktake_difference_idempotency_conflict"


def test_wrong_condition_is_one_conserved_misplacement_not_missing_plus_excess(world):
    task, round_row = _submitted(
        world,
        key="wrong-condition",
        counted_qty=None,
        draft=_managed_draft(world, material_id=world.material_a.id),
        account_counts=(
            StocktakeSnapshotCountInput(
                stock_account_id=world.region_new.id,
                counted_qty=Decimal("4.000"),
            ),
            StocktakeSnapshotCountInput(
                stock_account_id=world.region_used.id,
                counted_qty=Decimal("3.000"),
            ),
        ),
    )
    result = _evaluate(world, task, round_row, key="wrong-condition-evaluate")
    rows = tuple(world.db.scalars(select(StocktakeDifference)).all())

    assert result.difference_count == 1
    assert len(rows) == 1
    assert rows[0].difference_type == "wrong_condition"
    assert rows[0].expected_account_id == world.region_new.id
    assert rows[0].observed_account_id == world.region_used.id
    assert rows[0].book_qty == rows[0].counted_qty == Decimal("1.000")
    assert rows[0].difference_qty == Decimal("0.000")
    assert rows[0].affected_qty == Decimal("1.000")


@pytest.mark.parametrize(
    ("dimension", "expected_kind"),
    (("location", "wrong_location"), ("lot", "wrong_lot")),
)
def test_location_and_lot_misplacement_classification_is_dimension_exact(
    dimension,
    expected_kind,
):
    material_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    location_id = uuid.uuid4()
    lot_id = uuid.uuid4()
    common = {
        "scope_id": uuid.uuid4(),
        "account": None,
        "observation": None,
        "material_id": material_id,
        "owner_org_id": owner_id,
        "location_id": location_id,
        "custodian_person_id": None,
        "condition_code": "new",
        "availability_bucket": "available",
        "lot_id": lot_id,
        "remaining_qty": Decimal("1.000"),
    }
    expected = service._QuantitySide(**common)
    observed_values = dict(common)
    observed_values["scope_id"] = uuid.uuid4()
    if dimension == "location":
        observed_values["location_id"] = uuid.uuid4()
    else:
        observed_values["lot_id"] = uuid.uuid4()
    observed = service._QuantitySide(**observed_values)

    assert service._misplacement_kind(expected, observed) == expected_kind


def test_serial_substitution_is_missing_plus_unexpected_wrong_serial_and_conserved(world):
    unexpected = InventorySerial(
        id=uuid.uuid4(),
        material_id=world.material_b.id,
        serial_no="SN-UNEXPECTED-002",
        qr_code="QR-SN-UNEXPECTED-002",
        lot_id=None,
        lifecycle_status="active",
    )
    world.db.add(unexpected)
    world.db.flush()
    task, round_row = _submitted(
        world,
        key="serial-substitution",
        counted_qty=None,
        draft=_managed_draft(world, material_id=world.material_b.id),
        account_counts=(
            StocktakeSnapshotCountInput(
                stock_account_id=world.region_serial.id,
                counted_qty=Decimal("1"),
                count_method="scan",
                serial_ids=(unexpected.id,),
            ),
        ),
    )
    count_serial = world.db.scalar(select(StocktakeCountSerial))
    assert count_serial is not None and count_serial.result == "unexpected"

    result = _evaluate(world, task, round_row, key="serial-substitution-evaluate")
    rows = tuple(
        world.db.scalars(
            select(StocktakeDifference).order_by(StocktakeDifference.difference_no)
        ).all()
    )
    assert result.difference_count == 2
    assert {(row.difference_type, row.serial_id) for row in rows} == {
        ("missing", world.serial.id),
        ("wrong_serial", unexpected.id),
    }
    assert sum((row.difference_qty for row in rows), start=Decimal("0.000")) == Decimal(
        "0.000"
    )


@pytest.mark.parametrize("target", ("snapshot", "count"))
def test_snapshot_and_count_manifest_tampering_fail_closed(world, target):
    task, round_row = _submitted(
        world,
        key=f"tamper-{target}",
        counted_qty=Decimal("5.000"),
    )
    if target == "snapshot":
        snapshot = world.db.scalar(
            select(StocktakeSnapshotLine).where(
                StocktakeSnapshotLine.task_id == task.id
            )
        )
        assert snapshot is not None
        snapshot.book_qty = Decimal("6.000")
    else:
        count_line = world.db.scalar(
            select(StocktakeCountLine).where(StocktakeCountLine.task_id == task.id)
        )
        assert count_line is not None
        count_line.counted_qty = Decimal("4.000")
    world.db.flush()

    with pytest.raises(StocktakeDifferenceError) as failure:
        _evaluate(world, task, round_row, key=f"tamper-{target}-evaluate")
    assert failure.value.category == "service_unavailable"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeDifferenceSetCompletion)
    ) == 0


@pytest.mark.parametrize("target", ("authorization", "scope", "time"))
def test_forged_evaluator_completion_fails_replay_validation(world, target):
    task, round_row = _submitted(
        world,
        key=f"forged-{target}",
        counted_qty=Decimal("5.000"),
    )
    result = _evaluate(world, task, round_row, key=f"forged-{target}-evaluate")
    completion = world.db.get(StocktakeDifferenceSetCompletion, result.completion_id)
    assert completion is not None
    if target == "authorization":
        completion.authorization_sha256 = "f" * 64
    elif target == "scope":
        completion.scope_id_snapshot = str(world.region_x.id)
    else:
        completion.completed_at = NOW - timedelta(minutes=1)
        completion.created_at = completion.completed_at
    world.db.flush()

    with pytest.raises(StocktakeDifferenceError) as failure:
        _evaluate(world, task, round_row, key=f"forged-{target}-evaluate")
    assert failure.value.code == "stocktake_difference_replay_invalid"


def test_audit_failure_rolls_back_differences_and_completion(world, monkeypatch):
    task, round_row = _submitted(
        world,
        key="audit-failure",
        counted_qty=Decimal("4.000"),
    )
    writes_before = _write_counts(world)

    def _fail_audit(*_args, **_kwargs):
        raise AuditChainError("forced test failure")

    monkeypatch.setattr(service, "append_audit_event", _fail_audit)
    with pytest.raises(StocktakeDifferenceError) as failure:
        with world.db.begin_nested():
            _evaluate(world, task, round_row, key="audit-failure-evaluate")
    assert failure.value.code == "stocktake_difference_audit_chain_unavailable"
    assert world.db.scalar(select(func.count()).select_from(StocktakeDifference)) == 0
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeDifferenceSetCompletion)
    ) == 0
    assert _write_counts(world) == writes_before


def test_unknown_observation_stays_pending_excess_without_account_creation(world):
    account_count = world.db.scalar(select(func.count()).select_from(StockAccount))
    task, round_row = _submitted(
        world,
        key="unknown-observation",
        counted_qty=None,
        draft=_managed_draft(world, condition_code="damaged"),
        observations=(
            StocktakePhysicalObservationInput(
                material_id=None,
                material_identifier_raw="UNREADABLE-MATERIAL-1",
                material_identifier_type="unknown",
                condition_code="damaged",
                availability_bucket="available",
                counted_qty=Decimal("1.000"),
                serial_no_raw="UNREADABLE-SN-1",
                serial_identifier_type="unknown",
                count_method="scan",
            ),
        ),
    )
    result = _evaluate(world, task, round_row, key="unknown-observation-evaluate")

    observation = world.db.scalar(select(StocktakeCountObservation))
    difference = world.db.scalar(select(StocktakeDifference))
    assert observation is not None and difference is not None
    assert observation.verification_status == "pending_verification"
    assert observation.material_id is None and observation.serial_id is None
    assert result.pending_observation_difference_count == 1
    assert difference.difference_type == "excess"
    assert difference.reason_code == "stocktake_pending_verification"
    assert difference.material_id is None
    assert difference.observed_account_id is None
    assert difference.observed_line_id == observation.id
    assert world.db.scalar(select(func.count()).select_from(StockAccount)) == account_count


@pytest.mark.parametrize("actor", ("technician", "manager_y"))
def test_technician_and_wrong_region_manager_cannot_evaluate(world, actor):
    task, round_row = _submitted(
        world,
        key=f"forbidden-{actor}",
        counted_qty=Decimal("5.000"),
    )
    with pytest.raises(StocktakeDifferenceError) as failure:
        _evaluate(world, task, round_row, actor=actor, key=f"forbidden-{actor}-evaluate")
    assert failure.value.code == "stocktake_difference_forbidden"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeDifferenceSetCompletion)
    ) == 0


def test_count_locks_ledger_head_before_any_task_or_scope_row_lock():
    source = inspect.getsource(count_service._submit_stocktake_initial_scope_count)
    assert source.index("count_ledger_cursor = _lock_current_ledger_cursor(db)") < source.index(
        "select(FormalStocktakeTask)"
    )


def test_cutoff_replay_applies_quantity_movement_committed_before_count(world):
    created = _create_managed(
        world,
        key="replay-before-create",
        draft=_managed_draft(
            world,
            material_id=world.material_a.id,
            condition_code="new",
            freeze_mode="cutoff_replay",
        ),
    )
    started = _start(world, created.task_id, key="replay-before-start")
    scope = world.db.scalar(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == created.task_id
        )
    )
    assert scope is not None
    cursor = _append_ledger_movement(
        world,
        from_account=world.region_new,
        to_account=world.personal_account,
        quantity=Decimal("1.000"),
    )
    submit_stocktake_initial_scope_count(
        world.db,
        actor=world.principals["manager_x"],
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=created.task_id,
            round_id=started.initial_round_id,
            scope_id=scope.id,
            count_mode="blind",
            account_counts=(
                StocktakeSnapshotCountInput(
                    stock_account_id=world.region_new.id,
                    counted_qty=Decimal("4.000"),
                ),
            ),
        ),
        idempotency_key="replay-before-count",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-replay-before-count",
    )
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert completion is not None and completion.count_ledger_cursor == cursor

    task = world.db.get(FormalStocktakeTask, created.task_id)
    round_row = world.db.get(StocktakeRound, started.initial_round_id)
    assert task is not None and round_row is not None
    result = _evaluate(world, task, round_row, key="replay-before-evaluate")
    assert result.difference_count == 0


def test_cutoff_replay_ignores_quantity_movement_committed_after_count_boundary(world):
    task, round_row = _submitted(
        world,
        key="replay-after",
        counted_qty=Decimal("5.000"),
        draft=_managed_draft(
            world,
            material_id=world.material_a.id,
            condition_code="new",
            freeze_mode="cutoff_replay",
        ),
    )
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert completion is not None and completion.count_ledger_cursor == 1
    _append_ledger_movement(
        world,
        from_account=world.region_new,
        to_account=world.personal_account,
        quantity=Decimal("1.000"),
    )

    result = _evaluate(world, task, round_row, key="replay-after-evaluate")
    assert result.difference_count == 0


def test_hard_freeze_proves_no_post_cutoff_scope_movement(world):
    created = _create_managed(
        world,
        key="hard-proof-create",
        draft=_managed_draft(
            world,
            material_id=world.material_a.id,
            condition_code="new",
            freeze_mode="hard",
        ),
    )
    started = _start(world, created.task_id, key="hard-proof-start")
    scope = world.db.scalar(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == created.task_id
        )
    )
    assert scope is not None
    _append_ledger_movement(
        world,
        from_account=world.region_new,
        to_account=world.personal_account,
        quantity=Decimal("1.000"),
    )
    submit_stocktake_initial_scope_count(
        world.db,
        actor=world.principals["manager_x"],
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=created.task_id,
            round_id=started.initial_round_id,
            scope_id=scope.id,
            count_mode="blind",
            account_counts=(
                StocktakeSnapshotCountInput(
                    stock_account_id=world.region_new.id,
                    counted_qty=Decimal("4.000"),
                ),
            ),
        ),
        idempotency_key="hard-proof-count",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-hard-proof-count",
    )
    task = world.db.get(FormalStocktakeTask, created.task_id)
    round_row = world.db.get(StocktakeRound, started.initial_round_id)
    assert task is not None and round_row is not None

    with pytest.raises(StocktakeDifferenceError) as failure:
        _evaluate(world, task, round_row, key="hard-proof-evaluate")
    assert failure.value.code == "stocktake_difference_hard_freeze_ledger_changed"


def test_cutoff_replay_includes_post_cutoff_account_and_serial_position(world):
    created = _create_managed(
        world,
        key="replay-new-serial-create",
        draft=_managed_draft(
            world,
            material_id=world.material_b.id,
            freeze_mode="cutoff_replay",
        ),
    )
    started = _start(world, created.task_id, key="replay-new-serial-start")
    scope = world.db.scalar(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == created.task_id
        )
    )
    assert scope is not None
    new_account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=world.region_x.id,
        custodian_person_id=None,
        location_id=world.region_location.id,
        material_id=world.material_b.id,
        condition_code="used",
        availability_bucket="available",
        lot_id=None,
        created_at=NOW + timedelta(seconds=1),
        updated_at=NOW + timedelta(seconds=1),
    )
    world.db.add(new_account)
    world.db.flush()
    world.db.add(
        StockBalance(
            stock_account_id=new_account.id,
            quantity=Decimal("0.000"),
            ledger_cursor=1,
            version=0,
            created_at=NOW + timedelta(seconds=1),
            updated_at=NOW + timedelta(seconds=1),
        )
    )
    world.db.flush()
    cursor = _append_ledger_movement(
        world,
        from_account=world.region_serial,
        to_account=new_account,
        quantity=Decimal("1.000"),
        serial_ids=(world.serial.id,),
    )
    submit_stocktake_initial_scope_count(
        world.db,
        actor=world.principals["manager_x"],
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=created.task_id,
            round_id=started.initial_round_id,
            scope_id=scope.id,
            count_mode="blind",
            account_counts=(
                    StocktakeSnapshotCountInput(
                        stock_account_id=world.region_serial.id,
                        counted_qty=Decimal("0"),
                    ),
            ),
            physical_observations=(
                StocktakePhysicalObservationInput(
                    material_id=world.material_b.id,
                    material_identifier_raw=world.material_b.sku_code,
                    material_identifier_type="sku_code",
                    condition_code="used",
                    availability_bucket="available",
                    counted_qty=Decimal("1"),
                    serial_id=world.serial.id,
                    serial_no_raw=world.serial.serial_no,
                    serial_identifier_type="serial_no",
                    count_method="scan",
                ),
            ),
        ),
        idempotency_key="replay-new-serial-count",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-replay-new-serial-count",
    )
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert completion is not None and completion.count_ledger_cursor == cursor
    task = world.db.get(FormalStocktakeTask, created.task_id)
    round_row = world.db.get(StocktakeRound, started.initial_round_id)
    assert task is not None and round_row is not None

    result = _evaluate(world, task, round_row, key="replay-new-serial-evaluate")
    assert result.difference_count == 0


def test_old_completion_without_count_cursor_fails_closed(world):
    task, round_row = _submitted(
        world,
        key="legacy-null-cursor",
        counted_qty=Decimal("5.000"),
    )
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert completion is not None
    completion.count_ledger_cursor = None
    world.db.flush()

    with pytest.raises(StocktakeDifferenceError) as failure:
        _evaluate(world, task, round_row, key="legacy-null-cursor-evaluate")
    assert failure.value.code == "stocktake_difference_count_cursor_missing"


def test_count_idempotent_replay_keeps_original_cursor_after_unrelated_posting(world):
    created = _create_managed(
        world,
        key="count-cursor-replay-create",
        draft=_managed_draft(
            world,
            material_id=world.material_a.id,
            condition_code="new",
            freeze_mode="cutoff_replay",
        ),
    )
    started = _start(world, created.task_id, key="count-cursor-replay-start")
    scope = world.db.scalar(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == created.task_id
        )
    )
    assert scope is not None
    command = SubmitStocktakeInitialScopeCountCommand(
        task_id=created.task_id,
        round_id=started.initial_round_id,
        scope_id=scope.id,
        count_mode="blind",
        account_counts=(
            StocktakeSnapshotCountInput(
                stock_account_id=world.region_new.id,
                counted_qty=Decimal("5.000"),
            ),
        ),
    )
    first = submit_stocktake_initial_scope_count(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key="count-cursor-replay-count",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-count-cursor-replay-count",
    )
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert completion is not None and completion.count_ledger_cursor == 1
    _append_ledger_movement(
        world,
        from_account=world.personal_account,
        to_account=None,
        quantity=Decimal("1.000"),
    )
    replay = submit_stocktake_initial_scope_count(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key="count-cursor-replay-count",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-count-cursor-replay-count",
    )
    assert first.replayed is False and replay.replayed is True
    assert completion.count_ledger_cursor == 1


def test_cutoff_replay_mixed_scopes_use_the_same_committed_transfer_boundary(world):
    draft = StocktakeTaskCreateIn(
        task_type="sample",
        region_org_id=world.region_x.id,
        blind_count=True,
        scopes=(
            StocktakeScopeSelectionIn(
                owner_org_id=world.region_x.id,
                location_id=world.region_location.id,
                assignee_person_id=world.manager_x.person.id,
                scope_mode="filtered",
                material_id=world.material_a.id,
                condition_code="new",
                freeze_mode="cutoff_replay",
            ),
            StocktakeScopeSelectionIn(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_person_id=world.technician.person.id,
                scope_mode="filtered",
                material_id=world.material_a.id,
                condition_code="new",
                freeze_mode="cutoff_replay",
            ),
        ),
        deadline=NOW + timedelta(days=2),
        note="跨范围游标回放",
    )
    created = _create_managed(world, key="mixed-replay-create", draft=draft)
    started = _start(world, created.task_id, key="mixed-replay-start")
    cursor = _append_ledger_movement(
        world,
        from_account=world.region_new,
        to_account=world.personal_account,
        quantity=Decimal("1.000"),
    )
    scopes = tuple(
        world.db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == created.task_id)
            .order_by(FormalStocktakeScope.scope_no)
        ).all()
    )
    assert len(scopes) == 2
    scope_by_location = {row.location_id: row for row in scopes}
    for location_id, actor, account, quantity in (
        (
            world.region_location.id,
            world.principals["manager_x"],
            world.region_new,
            Decimal("4.000"),
        ),
        (
            world.personal_location.id,
            world.principals["technician"],
            world.personal_account,
            Decimal("4.000"),
        ),
    ):
        submit_stocktake_initial_scope_count(
            world.db,
            actor=actor,
            command=SubmitStocktakeInitialScopeCountCommand(
                task_id=created.task_id,
                round_id=started.initial_round_id,
                scope_id=scope_by_location[location_id].id,
                count_mode="blind",
                account_counts=(
                    StocktakeSnapshotCountInput(
                        stock_account_id=account.id,
                        counted_qty=quantity,
                    ),
                ),
            ),
            idempotency_key=f"mixed-replay-count-{location_id}",
            idempotency_hmac_secret=SECRET,
            trace_request_id=f"trace-mixed-replay-count-{location_id}",
        )
    completions = tuple(
        world.db.scalars(select(StocktakeScopeCountCompletion)).all()
    )
    assert {row.count_ledger_cursor for row in completions} == {cursor}
    task = world.db.get(FormalStocktakeTask, created.task_id)
    round_row = world.db.get(StocktakeRound, started.initial_round_id)
    assert task is not None and round_row is not None

    result = _evaluate(world, task, round_row, key="mixed-replay-evaluate")
    assert result.difference_count == 0


def test_cutoff_replay_scopes_use_their_own_count_cursors(world):
    draft = StocktakeTaskCreateIn(
        task_type="sample",
        region_org_id=world.region_x.id,
        blind_count=True,
        scopes=(
            StocktakeScopeSelectionIn(
                owner_org_id=world.region_x.id,
                location_id=world.region_location.id,
                assignee_person_id=world.manager_x.person.id,
                scope_mode="filtered",
                material_id=world.material_a.id,
                condition_code="new",
                freeze_mode="cutoff_replay",
            ),
            StocktakeScopeSelectionIn(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_person_id=world.technician.person.id,
                scope_mode="filtered",
                material_id=world.material_a.id,
                condition_code="new",
                freeze_mode="cutoff_replay",
            ),
        ),
        deadline=NOW + timedelta(days=2),
        note="跨范围异步游标回放",
    )
    created = _create_managed(world, key="async-replay-create", draft=draft)
    started = _start(world, created.task_id, key="async-replay-start")
    scopes = tuple(
        world.db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == created.task_id)
            .order_by(FormalStocktakeScope.scope_no)
        ).all()
    )
    scope_by_location = {row.location_id: row for row in scopes}

    submit_stocktake_initial_scope_count(
        world.db,
        actor=world.principals["manager_x"],
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=created.task_id,
            round_id=started.initial_round_id,
            scope_id=scope_by_location[world.region_location.id].id,
            count_mode="blind",
            account_counts=(
                StocktakeSnapshotCountInput(
                    stock_account_id=world.region_new.id,
                    counted_qty=Decimal("5.000"),
                ),
            ),
        ),
        idempotency_key="async-replay-region-count",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-async-replay-region-count",
    )
    transfer_cursor = _append_ledger_movement(
        world,
        from_account=world.region_new,
        to_account=world.personal_account,
        quantity=Decimal("1.000"),
    )
    submit_stocktake_initial_scope_count(
        world.db,
        actor=world.principals["technician"],
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=created.task_id,
            round_id=started.initial_round_id,
            scope_id=scope_by_location[world.personal_location.id].id,
            count_mode="blind",
            account_counts=(
                StocktakeSnapshotCountInput(
                    stock_account_id=world.personal_account.id,
                    counted_qty=Decimal("4.000"),
                ),
            ),
        ),
        idempotency_key="async-replay-personal-count",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-async-replay-personal-count",
    )
    completions = tuple(
        world.db.scalars(
            select(StocktakeScopeCountCompletion).order_by(
                StocktakeScopeCountCompletion.count_ledger_cursor
            )
        ).all()
    )
    assert [row.count_ledger_cursor for row in completions] == [1, transfer_cursor]
    task = world.db.get(FormalStocktakeTask, created.task_id)
    round_row = world.db.get(StocktakeRound, started.initial_round_id)
    assert task is not None and round_row is not None

    result = _evaluate(world, task, round_row, key="async-replay-evaluate")
    assert result.difference_count == 0


def test_cutoff_replay_rejects_count_cursor_ahead_of_ledger_head(world):
    task, round_row = _submitted(
        world,
        key="ahead-cursor",
        counted_qty=Decimal("5.000"),
        draft=_managed_draft(
            world,
            material_id=world.material_a.id,
            condition_code="new",
            freeze_mode="cutoff_replay",
        ),
    )
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    head = world.db.get(InventoryLedgerHead, task_service.INVENTORY_LEDGER_HEAD_ID)
    assert completion is not None and head is not None
    completion.count_ledger_cursor = head.next_cursor
    scopes = tuple(
        world.db.scalars(
            select(FormalStocktakeScope).where(
                FormalStocktakeScope.task_id == task.id
            )
        ).all()
    )
    plans = task_service._load_and_validate_scope_plans(
        world.db,
        task,
        scopes,
        now=EVALUATED_AT,
    )
    snapshots = tuple(
        world.db.scalars(
            select(StocktakeSnapshotLine).where(
                StocktakeSnapshotLine.task_id == task.id
            )
        ).all()
    )
    accounts = count_service._lock_snapshot_accounts(world.db, snapshots)

    with pytest.raises(StocktakeDifferenceError) as failure:
        service._replay_scope_expected_states(
            world.db,
            task=task,
            scopes=scopes,
            plans=plans,
            snapshots=snapshots,
            snapshot_accounts=accounts,
            observations=(),
            completions=(completion,),
        )
    assert failure.value.code == "stocktake_difference_count_cursor_ahead"


def test_cutoff_replay_rejects_serial_movement_without_complete_serial_rows(world):
    created = _create_managed(
        world,
        key="serial-coverage-create",
        draft=_managed_draft(
            world,
            material_id=world.material_b.id,
            freeze_mode="cutoff_replay",
        ),
    )
    started = _start(world, created.task_id, key="serial-coverage-start")
    scope = world.db.scalar(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == created.task_id
        )
    )
    assert scope is not None
    _append_ledger_movement(
        world,
        from_account=world.region_serial,
        to_account=None,
        quantity=Decimal("1.000"),
    )
    submit_stocktake_initial_scope_count(
        world.db,
        actor=world.principals["manager_x"],
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=created.task_id,
            round_id=started.initial_round_id,
            scope_id=scope.id,
            count_mode="blind",
            account_counts=(
                StocktakeSnapshotCountInput(
                    stock_account_id=world.region_serial.id,
                    counted_qty=Decimal("0"),
                ),
            ),
        ),
        idempotency_key="serial-coverage-count",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-serial-coverage-count",
    )
    task = world.db.get(FormalStocktakeTask, created.task_id)
    round_row = world.db.get(StocktakeRound, started.initial_round_id)
    assert task is not None and round_row is not None

    with pytest.raises(StocktakeDifferenceError) as failure:
        _evaluate(world, task, round_row, key="serial-coverage-evaluate")
    assert (
        failure.value.code
        == "stocktake_difference_replay_serial_quantity_mismatch"
    )


def test_cutoff_replay_rejects_movement_between_different_materials(world):
    created = _create_managed(
        world,
        key="movement-material-create",
        draft=_managed_draft(
            world,
            material_id=world.material_a.id,
            condition_code="new",
            freeze_mode="cutoff_replay",
        ),
    )
    started = _start(world, created.task_id, key="movement-material-start")
    scope = world.db.scalar(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == created.task_id
        )
    )
    assert scope is not None
    _append_ledger_movement(
        world,
        from_account=world.region_new,
        to_account=world.region_serial,
        quantity=Decimal("1.000"),
    )
    submit_stocktake_initial_scope_count(
        world.db,
        actor=world.principals["manager_x"],
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=created.task_id,
            round_id=started.initial_round_id,
            scope_id=scope.id,
            count_mode="blind",
            account_counts=(
                StocktakeSnapshotCountInput(
                    stock_account_id=world.region_new.id,
                    counted_qty=Decimal("4.000"),
                ),
            ),
        ),
        idempotency_key="movement-material-count",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-movement-material-count",
    )
    task = world.db.get(FormalStocktakeTask, created.task_id)
    round_row = world.db.get(StocktakeRound, started.initial_round_id)
    assert task is not None and round_row is not None

    with pytest.raises(StocktakeDifferenceError) as failure:
        _evaluate(world, task, round_row, key="movement-material-evaluate")
    assert (
        failure.value.code
        == "stocktake_difference_replay_movement_material_mismatch"
    )
