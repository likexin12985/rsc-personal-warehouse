from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import func, select

import app.formal_services.stocktake_count as service
import app.formal_services.stocktake_task as task_service
from app.formal_services import formal_files
from app.formal_services.stocktake_count import (
    StocktakeCountError,
    StocktakePhysicalObservationInput,
    StocktakeSnapshotCountInput,
    SubmitStocktakeInitialScopeCountCommand,
    submit_stocktake_initial_scope_count,
)
from app.foundation_models import (
    AuditEvent,
    DocumentAttachment,
    FileObject,
    OutboxEvent,
    StateTransitionEvent,
)
from app.inventory_models import (
    InventoryMovement,
    InventoryTransaction,
    MaterialInventoryPolicy,
    QrCode,
    StockAccount,
)
from app.stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    StocktakeCountLine,
    StocktakeCountObservation,
    StocktakeCountSerial,
    StocktakeDifference,
    StocktakeRound,
    StocktakeRoundSubmission,
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
    _termination_draft,
    db,
    world,
)


@pytest.fixture(autouse=True)
def _fixed_count_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_database_now", lambda _db: NOW)
    monkeypatch.setattr(task_service, "_database_now", lambda _db: NOW)


def _started(world, *, key: str, draft: StocktakeTaskCreateIn):
    created = _create_managed(world, key=f"{key}-create", draft=draft)
    started = _start(world, created.task_id, key=f"{key}-start")
    scopes = tuple(
        world.db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == created.task_id)
            .order_by(FormalStocktakeScope.scope_no)
        ).all()
    )
    round_row = world.db.get(StocktakeRound, started.initial_round_id)
    assert round_row is not None
    return created, started, scopes, round_row


def _submit(
    world,
    *,
    command: SubmitStocktakeInitialScopeCountCommand,
    key: str,
    actor_name: str = "manager_x",
):
    return submit_stocktake_initial_scope_count(
        world.db,
        actor=world.principals[actor_name],
        command=command,
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
    )


def test_blind_scope_submission_seals_initial_round_without_inventory_or_difference(world):
    created, started, scopes, round_row = _started(
        world,
        key="blind",
        draft=_managed_draft(
            world,
            material_id=world.material_a.id,
            condition_code="new",
        ),
    )
    snapshot = world.db.scalar(
        select(StocktakeSnapshotLine).where(
            StocktakeSnapshotLine.task_id == created.task_id
        )
    )
    assert snapshot is not None
    snapshot_document = (
        snapshot.book_qty,
        snapshot.ledger_cursor,
        snapshot.account_dimension_sha256,
        snapshot.serial_snapshot_sha256,
    )
    tx_before = world.db.scalar(select(func.count()).select_from(InventoryTransaction))
    movement_before = world.db.scalar(select(func.count()).select_from(InventoryMovement))

    command = SubmitStocktakeInitialScopeCountCommand(
        task_id=created.task_id,
        round_id=round_row.id,
        scope_id=scopes[0].id,
        count_mode="blind",
        account_counts=(
            StocktakeSnapshotCountInput(
                stock_account_id=world.region_new.id,
                counted_qty=Decimal("4.000"),
                count_method="manual",
                reason_code="physical_count",
            ),
        ),
    )
    with pytest.raises(StocktakeCountError) as failure:
        _submit(
            world,
            command=replace(
                command,
                account_counts=(
                    replace(
                        command.account_counts[0],
                        book_qty_confirmation=Decimal("5.000"),
                    ),
                ),
            ),
            key="blind-leaks-book-qty",
        )
    assert failure.value.code == "stocktake_count_blind_book_qty_forbidden"
    result = _submit(world, command=command, key="blind-count")

    task = world.db.get(FormalStocktakeTask, created.task_id)
    world.db.refresh(round_row)
    world.db.refresh(snapshot)
    assert task is not None
    assert result.task_status == task.status == "submitted"
    assert result.round_status == round_row.status == "submitted"
    assert result.task_version == task.version == 2
    assert result.round_submitted is True
    assert snapshot_document == (
        snapshot.book_qty,
        snapshot.ledger_cursor,
        snapshot.account_dimension_sha256,
        snapshot.serial_snapshot_sha256,
    )
    assert world.db.scalar(select(func.count()).select_from(StocktakeCountLine)) == 1
    assert world.db.scalar(select(func.count()).select_from(StocktakeScopeCountCompletion)) == 1
    assert world.db.scalar(select(func.count()).select_from(StocktakeRoundSubmission)) == 1
    assert world.db.scalar(select(func.count()).select_from(StocktakeDifference)) == 0
    assert world.db.scalar(select(func.count()).select_from(InventoryTransaction)) == tx_before
    assert world.db.scalar(select(func.count()).select_from(InventoryMovement)) == movement_before
    assert world.db.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    assert {
        row.action
        for row in world.db.scalars(
            select(AuditEvent).where(AuditEvent.stream_key == "inventory")
        ).all()
    } >= {
        "stocktake.scope_count.submitted",
        "stocktake.initial_round.submitted",
    }
    assert {
        (row.aggregate_type, row.from_status, row.to_status)
        for row in world.db.scalars(
            select(StateTransitionEvent).where(
                StateTransitionEvent.occurred_at == NOW
            )
        ).all()
    } >= {
        ("stocktake_scope", "counting", "completed"),
        ("stocktake_round", "counting", "submitted"),
        ("stocktake_task", "counting", "submitted"),
    }

    replay = _submit(world, command=command, key="blind-count")
    assert replay == replace(result, replayed=True)
    changed = replace(
        command,
        account_counts=(
            replace(command.account_counts[0], counted_qty=Decimal("3.000")),
        ),
    )
    with pytest.raises(StocktakeCountError) as failure:
        _submit(world, command=changed, key="blind-count")
    assert failure.value.code == "stocktake_count_idempotency_conflict"


def test_open_and_blind_contracts_are_bound_to_task_mode_and_snapshot(world):
    open_draft = _managed_draft(
        world,
        material_id=world.material_a.id,
        condition_code="new",
    ).model_copy(update={"blind_count": False})
    created, _started_row, scopes, round_row = _started(
        world,
        key="open",
        draft=open_draft,
    )
    missing_confirmation = SubmitStocktakeInitialScopeCountCommand(
        task_id=created.task_id,
        round_id=round_row.id,
        scope_id=scopes[0].id,
        count_mode="open",
        account_counts=(
            StocktakeSnapshotCountInput(
                stock_account_id=world.region_new.id,
                counted_qty=Decimal("5.000"),
            ),
        ),
    )
    with pytest.raises(StocktakeCountError) as failure:
        _submit(world, command=missing_confirmation, key="open-missing")
    assert failure.value.code == "stocktake_count_open_book_qty_mismatch"

    wrong_mode = replace(missing_confirmation, count_mode="blind")
    with pytest.raises(StocktakeCountError) as failure:
        _submit(world, command=wrong_mode, key="open-wrong-mode")
    assert failure.value.code == "stocktake_count_mode_task_mismatch"

    exact = replace(
        missing_confirmation,
        account_counts=(
            replace(
                missing_confirmation.account_counts[0],
                book_qty_confirmation=Decimal("5.000"),
            ),
        ),
    )
    assert _submit(world, command=exact, key="open-exact").round_submitted is True


def test_each_scope_is_complete_but_task_submits_only_after_all_scopes(world):
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
                freeze_mode="hard",
            ),
            StocktakeScopeSelectionIn(
                owner_org_id=world.region_x.id,
                location_id=world.region_location.id,
                assignee_person_id=world.manager_x.person.id,
                scope_mode="filtered",
                material_id=world.material_a.id,
                condition_code="used",
                freeze_mode="hard",
            ),
        ),
        deadline=None,
        note="两范围完整提交",
    )
    created, _started_row, scopes, round_row = _started(
        world,
        key="two-scopes",
        draft=draft,
    )
    first = _submit(
        world,
        key="two-scopes-first",
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=created.task_id,
            round_id=round_row.id,
            scope_id=scopes[0].id,
            count_mode="blind",
            account_counts=(
                StocktakeSnapshotCountInput(
                    stock_account_id=world.region_new.id,
                    counted_qty=Decimal("5.000"),
                ),
            ),
        ),
    )
    assert first.round_submitted is False
    assert first.task_status == first.round_status == "counting"
    assert world.db.scalar(select(func.count()).select_from(StocktakeRoundSubmission)) == 0

    second = _submit(
        world,
        key="two-scopes-second",
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=created.task_id,
            round_id=round_row.id,
            scope_id=scopes[1].id,
            count_mode="blind",
            account_counts=(
                StocktakeSnapshotCountInput(
                    stock_account_id=world.region_used.id,
                    counted_qty=Decimal("1.000"),
                ),
            ),
        ),
    )
    assert second.round_submitted is True
    assert second.task_status == second.round_status == "submitted"
    submission = world.db.scalar(select(StocktakeRoundSubmission))
    assert submission is not None
    assert submission.scope_count == 2
    assert submission.count_line_count == 2


def test_assigned_region_manager_can_submit_termination_personal_scope(world):
    created, _started_row, scopes, round_row = _started(
        world,
        key="termination-count",
        draft=_termination_draft(world),
    )

    result = _submit(
        world,
        key="termination-count-submit",
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=created.task_id,
            round_id=round_row.id,
            scope_id=scopes[0].id,
            count_mode="blind",
            account_counts=(
                StocktakeSnapshotCountInput(
                    stock_account_id=world.personal_account.id,
                    counted_qty=Decimal("3.000"),
                ),
            ),
        ),
    )

    completion = world.db.scalar(
        select(StocktakeScopeCountCompletion).where(
            StocktakeScopeCountCompletion.task_id == created.task_id
        )
    )
    assert result.round_submitted is True
    assert completion is not None
    assert completion.completed_by_user_id == world.manager_x.user.id
    assert completion.role_code == "provincial_manager"
    assert completion.scope_type == "organization"
    assert completion.scope_id_snapshot == str(world.region_x.id)


def test_sn_is_counted_piecewise_once_and_bound_to_cutoff_policy(world):
    created, _started_row, scopes, round_row = _started(
        world,
        key="serial",
        draft=_managed_draft(world, material_id=world.material_b.id),
    )
    command = SubmitStocktakeInitialScopeCountCommand(
        task_id=created.task_id,
        round_id=round_row.id,
        scope_id=scopes[0].id,
        count_mode="blind",
        account_counts=(
            StocktakeSnapshotCountInput(
                stock_account_id=world.region_serial.id,
                counted_qty=Decimal("1"),
                count_method="scan",
                serial_ids=(world.serial.id,),
            ),
        ),
    )
    _submit(world, command=command, key="serial-count")
    row = world.db.scalar(select(StocktakeCountSerial))
    assert row is not None
    assert row.serial_id == world.serial.id
    assert row.result == "present"

    duplicate_command = replace(
        command,
        account_counts=(
            replace(command.account_counts[0], serial_ids=(world.serial.id, world.serial.id)),
        ),
    )
    with pytest.raises(StocktakeCountError) as failure:
        _submit(world, command=duplicate_command, key="serial-duplicate")
    assert failure.value.code == "stocktake_count_serial_duplicate"


def test_available_owned_evidence_files_are_bound_to_immutable_scope_manifest(world):
    created, _started_row, scopes, round_row = _started(
        world,
        key="evidence",
        draft=_managed_draft(
            world,
            material_id=world.material_a.id,
            condition_code="new",
        ),
    )
    file_id = uuid.uuid4()
    storage_key = (
        "formal-files/v1/stocktake_evidence/"
        f"{file_id.hex[:2]}/{file_id.hex}"
    )
    file_row = FileObject(
        id=file_id,
        storage_key=storage_key,
        sha256="a" * 64,
        size_bytes=1024,
        mime_type="image/jpeg",
        original_filename="photo-1.jpg",
        uploaded_by=world.manager_x.user.id,
        status="available",
        metadata_jsonb={
            "authorization_version": world.manager_x.user.authorization_version,
            "file_id": str(file_id),
            "idempotency_key_hash": "b" * 64,
            "provider": "test_formal_storage",
            "purpose": "stocktake_evidence",
            "request_sha256": formal_files._upload_request_hash(
                formal_files._PreparedUpload(
                    purpose="stocktake_evidence",
                    original_filename="photo-1.jpg",
                    size_bytes=1024,
                    mime_type="image/jpeg",
                    sha256="a" * 64,
                )
            ),
            "schema": "cloud_oam.formal_file_upload_intent.v1",
            "storage_key": storage_key,
            "uploader_person_id": str(world.manager_x.person.id),
            "uploader_user_id": world.manager_x.user.id,
            "completion": {
                "etag_sha256": "d" * 64,
                "head_manifest_sha256": "e" * 64,
                "verified_at": NOW.isoformat(),
            },
        },
    )
    world.db.add(file_row)
    world.db.flush()
    quarantined = FileObject(
        id=uuid.uuid4(),
        storage_key="stocktake/evidence/quarantined.jpg",
        sha256="b" * 64,
        size_bytes=512,
        mime_type="image/jpeg",
        original_filename="quarantined.jpg",
        uploaded_by=world.manager_x.user.id,
        status="quarantined",
        metadata_jsonb={},
    )
    world.db.add(quarantined)
    world.db.flush()
    with pytest.raises(StocktakeCountError) as failure:
        _submit(
            world,
            key="evidence-quarantined",
            command=SubmitStocktakeInitialScopeCountCommand(
                task_id=created.task_id,
                round_id=round_row.id,
                scope_id=scopes[0].id,
                count_mode="blind",
                account_counts=(
                    StocktakeSnapshotCountInput(
                        stock_account_id=world.region_new.id,
                        counted_qty=Decimal("5.000"),
                    ),
                ),
                evidence_file_ids=(quarantined.id,),
            ),
        )
    assert failure.value.code == "stocktake_count_evidence_file_invalid"
    result = _submit(
        world,
        key="evidence-count",
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=created.task_id,
            round_id=round_row.id,
            scope_id=scopes[0].id,
            count_mode="blind",
            account_counts=(
                StocktakeSnapshotCountInput(
                    stock_account_id=world.region_new.id,
                    counted_qty=Decimal("5.000"),
                    count_method="scan",
                ),
            ),
            evidence_file_ids=(file_row.id,),
        ),
    )
    attachment = world.db.scalar(select(DocumentAttachment))
    assert result.evidence_file_count == 1
    assert attachment is not None
    assert attachment.document_type == "stocktake_scope_count_completion"
    assert attachment.file_id == file_row.id
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert completion is not None and len(completion.evidence_manifest_sha256) == 64


def test_unknown_material_lot_and_serial_are_retained_pending_without_empty_account(world):
    # Unknown material in a condition-only scope remains raw evidence.
    created, _started_row, scopes, round_row = _started(
        world,
        key="pending-material",
        draft=_managed_draft(world, condition_code="damaged"),
    )
    account_count_before = world.db.scalar(
        select(func.count()).select_from(StockAccount)
    )
    _submit(
        world,
        key="pending-material-count",
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=created.task_id,
            round_id=round_row.id,
            scope_id=scopes[0].id,
            count_mode="blind",
            physical_observations=(
                StocktakePhysicalObservationInput(
                    material_id=None,
                    material_identifier_raw="UNREADABLE-MAT-7788",
                    material_identifier_type="unknown",
                    condition_code="damaged",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                    lot_no_raw="RAW-LOT-UNKNOWN",
                    serial_no_raw="RAW-SN-UNKNOWN",
                    serial_identifier_type="unknown",
                    count_method="scan",
                    reason_code="unresolved_identifier",
                ),
            ),
        ),
    )
    observation = world.db.scalar(select(StocktakeCountObservation))
    assert observation is not None
    assert observation.verification_status == "pending_verification"
    assert observation.material_id is None
    assert observation.lot_id is None and observation.lot_no_raw == "RAW-LOT-UNKNOWN"
    assert observation.serial_id is None and observation.serial_no_raw == "RAW-SN-UNKNOWN"
    assert world.db.scalar(select(func.count()).select_from(StockAccount)) == account_count_before
    assert world.db.scalar(select(func.count()).select_from(StocktakeDifference)) == 0
    assert world.db.scalar(select(func.count()).select_from(InventoryTransaction)) == 1


def test_unresolved_lot_and_sn_on_known_material_are_retained_pending(world):
    # Convert fixture material A into a lot-tracked material for an empty
    # condition-filtered scope.  No existing account is selected by the task.
    policy_a = world.db.scalar(
        select(MaterialInventoryPolicy).where(
            MaterialInventoryPolicy.material_id == world.material_a.id
        )
    )
    assert policy_a is not None
    policy_a.tracking_mode = "lot"
    policy_a.quantity_scale = 3
    policy_a.allow_fraction = True
    world.db.flush()
    created, _started_row, scopes, round_row = _started(
        world,
        key="pending-lot",
        draft=_managed_draft(
            world,
            material_id=world.material_a.id,
            condition_code="damaged",
        ),
    )
    _submit(
        world,
        key="pending-lot-count",
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=created.task_id,
            round_id=round_row.id,
            scope_id=scopes[0].id,
            count_mode="blind",
            physical_observations=(
                StocktakePhysicalObservationInput(
                    material_id=world.material_a.id,
                    material_identifier_raw=world.material_a.sku_code,
                    material_identifier_type="sku_code",
                    condition_code="damaged",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                    lot_no_raw="LOT-NOT-IN-MASTER",
                ),
            ),
        ),
    )
    lot_observation = world.db.scalar(select(StocktakeCountObservation))
    assert lot_observation is not None
    assert lot_observation.material_id == world.material_a.id
    assert lot_observation.lot_id is None
    assert lot_observation.verification_status == "pending_verification"


def test_unresolved_sn_on_known_serial_material_is_retained_piecewise(world):
    created, _started_row, scopes, round_row = _started(
        world,
        key="pending-sn",
        draft=_managed_draft(
            world,
            material_id=world.material_b.id,
            condition_code="used",
        ),
    )
    _submit(
        world,
        key="pending-sn-count",
        command=SubmitStocktakeInitialScopeCountCommand(
            task_id=created.task_id,
            round_id=round_row.id,
            scope_id=scopes[0].id,
            count_mode="blind",
            physical_observations=(
                StocktakePhysicalObservationInput(
                    material_id=world.material_b.id,
                    material_identifier_raw=world.material_b.sku_code,
                    material_identifier_type="sku_code",
                    condition_code="used",
                    availability_bucket="available",
                    counted_qty=Decimal("1"),
                    serial_no_raw="SN-NOT-IN-MASTER",
                    serial_identifier_type="unknown",
                    count_method="scan",
                ),
            ),
        ),
    )
    observation = world.db.scalar(select(StocktakeCountObservation))
    assert observation is not None
    assert observation.material_id == world.material_b.id
    assert observation.serial_id is None
    assert observation.serial_no_raw == "SN-NOT-IN-MASTER"
    assert observation.verification_status == "pending_verification"


def test_ambiguous_unknown_material_identifier_and_stale_or_wrong_actor_fail_closed(world):
    created, _started_row, scopes, round_row = _started(
        world,
        key="ambiguous",
        draft=_managed_draft(world, condition_code="damaged"),
    )
    world.db.add(
        QrCode(
            id=uuid.uuid4(),
            code=world.material_a.sku_code,
            object_type="material",
            object_id=world.material_b.id,
            status="active",
            printed_at=NOW,
        )
    )
    world.db.flush()
    command = SubmitStocktakeInitialScopeCountCommand(
        task_id=created.task_id,
        round_id=round_row.id,
        scope_id=scopes[0].id,
        count_mode="blind",
        physical_observations=(
            StocktakePhysicalObservationInput(
                material_id=None,
                material_identifier_raw=world.material_a.sku_code,
                material_identifier_type="unknown",
                condition_code="damaged",
                availability_bucket="available",
                counted_qty=Decimal("1.000"),
            ),
        ),
    )
    with pytest.raises(StocktakeCountError) as failure:
        _submit(world, command=command, key="ambiguous-count")
    assert failure.value.code == "stocktake_count_material_identifier_ambiguous"

    with pytest.raises(StocktakeCountError) as failure:
        _submit(world, command=command, key="wrong-actor", actor_name="manager_y")
    assert failure.value.code == "stocktake_count_not_assignee"

    stale = replace(
        world.principals["manager_x"],
        authorization_version=world.principals["manager_x"].authorization_version + 1,
    )
    with pytest.raises(StocktakeCountError) as failure:
        submit_stocktake_initial_scope_count(
            world.db,
            actor=stale,
            command=command,
            idempotency_key="stale-actor",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-stale-actor",
        )
    assert failure.value.code == "stocktake_count_actor_principal_stale"
