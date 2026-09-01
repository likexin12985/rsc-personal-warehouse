from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import inspect
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.formal_services.opening_observation_disposition as disposition_service
import app.formal_services.opening_stocktake as opening_service
import app.formal_services.opening_stocktake_count as count_service
from app.formal_services.opening_observation_disposition import (
    OpeningObservationDispositionError,
    RecordOpeningObservationDispositionCommand,
    record_opening_observation_disposition,
)
from app.formal_services.opening_stocktake_count import OpeningPhysicalObservationInput
from app.foundation_models import AuditEvent, ExternalObject, OutboxEvent, StateTransitionEvent
from app.inventory_models import (
    FormalMaterial,
    InventoryLot,
    InventoryMovement,
    InventorySerial,
    InventoryTransaction,
    QrCode,
    StockAccount,
    StockBalance,
)
from app.stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryOpeningEstablishment,
    StocktakeDifferenceSetCompletion,
    StocktakeObservationDisposition,
    StocktakePosting,
    StocktakeReview,
)

import test_opening_stocktake_service as support


NOW = support.NOW
DISPOSITION_NOW = NOW + timedelta(hours=2)


@pytest.fixture(autouse=True)
def _fixed_database_times(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opening_service, "_database_now", lambda _db: NOW)
    monkeypatch.setattr(
        count_service,
        "_database_now",
        lambda _db: NOW + timedelta(hours=1),
    )
    monkeypatch.setattr(
        disposition_service,
        "_database_now",
        lambda _db: DISPOSITION_NOW,
    )


@pytest.fixture
def db() -> Session:
    yield from support.db.__wrapped__()


@pytest.fixture
def world(db: Session) -> SimpleNamespace:
    return support.world.__wrapped__(db)


def _prepare_pending(
    world: SimpleNamespace,
    *,
    material_raw: str = "PENDING-SKU-001",
    material_type: str = "sku_code",
    lot_raw: str | None = None,
    serial_raw: str | None = None,
    serial_type: str | None = None,
    counted_qty: Decimal = Decimal("1.000"),
) -> SimpleNamespace:
    started = support._start(
        world,
        key="opening-disposition-start-0001",
    )
    support._submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=support._scope_count_command(
            world,
            started,
            observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw=material_raw,
                    material_identifier_type=material_type,
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=counted_qty,
                    lot_no_raw=lot_raw,
                    serial_no_raw=serial_raw,
                    serial_identifier_type=serial_type,
                    count_method="manual",
                    remark="现场待核实",
                ),
            ),
        ),
        key="opening-disposition-count-0001",
    )
    observation = world.db.scalar(
        select(support.StocktakeCountObservation).where(
            support.StocktakeCountObservation.task_id == started.task_id,
            support.StocktakeCountObservation.material_identifier_raw == material_raw,
        )
    )
    assert observation is not None
    assert observation.verification_status == "pending_verification"
    task = world.db.get(FormalStocktakeTask, started.task_id)
    scope = world.db.get(FormalStocktakeScope, observation.scope_id)
    assert task is not None and scope is not None
    return SimpleNamespace(
        started=started,
        task=task,
        scope=scope,
        observation=observation,
    )


def _command(
    prepared: SimpleNamespace,
    *,
    disposition: str = "pending_verification",
    reason_code: str = "site_identifier_pending",
    comment: str = "保留原始证据，继续人工核验",
    material_id: uuid.UUID | None = None,
    lot_id: uuid.UUID | None = None,
    serial_id: uuid.UUID | None = None,
) -> RecordOpeningObservationDispositionCommand:
    return RecordOpeningObservationDispositionCommand(
        task_id=prepared.started.task_id,
        round_id=prepared.started.initial_round_id,
        observation_id=prepared.observation.id,
        disposition=disposition,
        reason_code=reason_code,
        comment=comment,
        resolved_material_id=material_id,
        resolved_lot_id=lot_id,
        resolved_serial_id=serial_id,
    )


def _record(
    world: SimpleNamespace,
    prepared: SimpleNamespace,
    *,
    actor_name: str = "manager_x",
    command: RecordOpeningObservationDispositionCommand | None = None,
    key: str = "opening-disposition-idempotency-0001",
    request_id: str = "opening-disposition-request-0001",
):
    return record_opening_observation_disposition(
        world.db,
        actor=world.principals[actor_name],
        command=command or _command(prepared),
        idempotency_key=key,
        request_id=request_id,
    )


def _error_code(callable_) -> str:
    with pytest.raises(OpeningObservationDispositionError) as captured:
        callable_()
    assert "sqlite" not in captured.value.message.lower()
    assert "insert" not in captured.value.message.lower()
    return captured.value.code


def _add_material(
    world: SimpleNamespace,
    *,
    sku_code: str,
    tracking_mode: str,
) -> FormalMaterial:
    material = support._material(world.db, world.source, tracking_mode)
    material.sku_code = sku_code
    world.db.flush()
    return material


def _inventory_counts(db: Session) -> dict[type[object], int]:
    return {
        model: db.scalar(select(func.count()).select_from(model)) or 0
        for model in (
            FormalMaterial,
            InventoryLot,
            InventorySerial,
            StockAccount,
            StockBalance,
            InventoryTransaction,
            InventoryMovement,
            InventoryOpeningEstablishment,
        )
    }


def test_disposition_locks_one_complete_0027_reference_graph_in_order(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepare_pending(world)
    calls: list[str] = []
    locked_user_ids: set[str] = set()
    real_principal = disposition_service.lock_formal_principal_graph
    real_evidence = disposition_service.lock_opening_stocktake_task_evidence

    def record_principal(session, user_ids):
        calls.append("principal")
        locked_user_ids.update(user_ids)
        return real_principal(session, user_ids)

    def record_evidence(*args, **kwargs):
        calls.append("evidence")
        return real_evidence(*args, **kwargs)

    monkeypatch.setattr(
        disposition_service,
        "lock_formal_principal_graph",
        record_principal,
    )
    monkeypatch.setattr(
        disposition_service,
        "lock_opening_stocktake_task_evidence",
        record_evidence,
    )
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

    result = _record(
        world,
        prepared,
        key="opening-disposition-single-reference-graph",
    )

    assert result.replayed is False
    assert world.principals["manager_x"].user_id in locked_user_ids
    assert calls == ["principal", "evidence", "start", "inventory", "serial"]


def test_exact_owner_manager_can_keep_pending_without_inventory_or_state_side_effects(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepare_pending(world)
    inventory_before = _inventory_counts(world.db)
    state_before = world.db.scalar(select(func.count()).select_from(StateTransitionEvent))
    outbox_before = world.db.scalar(select(func.count()).select_from(OutboxEvent))
    task_version = prepared.task.version

    monkeypatch.setattr(
        world.db,
        "commit",
        lambda: pytest.fail("disposition service must not commit"),
    )
    monkeypatch.setattr(
        world.db,
        "rollback",
        lambda: pytest.fail("disposition service must not roll back"),
    )
    result = _record(world, prepared)

    row = world.db.get(StocktakeObservationDisposition, result.disposition_id)
    assert row is not None
    assert result.replayed is False
    assert row.role_code == "provincial_manager"
    assert row.scope_type == "organization"
    assert uuid.UUID(row.scope_id_snapshot) == prepared.observation.owner_org_id
    assert row.resolved_material_id is None
    assert row.resolved_lot_id is None
    assert row.resolved_serial_id is None
    assert len(row.authorization_sha256) == 64
    assert len(row.disposition_manifest_sha256) == 64
    assert _inventory_counts(world.db) == inventory_before
    assert prepared.task.status == "submitted"
    assert prepared.task.version == task_version
    assert world.db.scalar(select(func.count()).select_from(StateTransitionEvent)) == state_before
    assert world.db.scalar(select(func.count()).select_from(OutboxEvent)) == outbox_before
    audits = world.db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == "stocktake.opening.observation_disposed"
        )
    ).all()
    assert len(audits) == 1
    assert audits[0].after_jsonb["disposition_manifest_sha256"] == row.disposition_manifest_sha256


def test_admin_can_choose_pending_or_requires_recount(world: SimpleNamespace) -> None:
    prepared = _prepare_pending(world)
    result = _record(
        world,
        prepared,
        actor_name="admin",
        command=_command(
            prepared,
            disposition="requires_recount",
            reason_code="physical_recount_required",
            comment="现场证据存在冲突，必须受控复盘",
        ),
    )
    row = world.db.get(StocktakeObservationDisposition, result.disposition_id)
    assert row is not None
    assert row.role_code == "admin"
    assert row.scope_type == "national"
    assert row.scope_id_snapshot == "*"
    assert row.disposition == "requires_recount"


def test_cross_owner_manager_is_forbidden(world: SimpleNamespace) -> None:
    prepared = _prepare_pending(world)
    assert _error_code(
        lambda: _record(world, prepared, actor_name="manager_y")
    ) == "opening_observation_disposition_forbidden"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeObservationDisposition)
    ) == 0


def test_known_observation_uuid_cannot_union_task_owner_and_location_grants(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_pending(world, material_raw="PENDING-CROSS-SCOPE-UUID")
    # Reproduce the unsafe shape of a legacy/prototype record: the task and
    # physical location remain in region X while the persisted asset owner was
    # changed to region Y.  A region-Y manager knows the exact observation UUID
    # and has manage permission for that owner, but no single assignment covers
    # all three dimensions.
    prepared.scope.owner_org_id = world.region_y.id
    prepared.observation.owner_org_id = world.region_y.id
    world.db.flush()
    assert world.principals["manager_y"].allows(
        world.db,
        "stocktake",
        "manage",
        target_scope_type="organization",
        target_scope_id=str(world.region_y.id),
    )

    assert _error_code(
        lambda: _record(
            world,
            prepared,
            actor_name="manager_y",
            key="opening-disposition-known-cross-scope-uuid",
            request_id="opening-disposition-known-cross-scope-uuid-request",
        )
    ) == "opening_observation_disposition_forbidden"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeObservationDisposition)
    ) == 0


def test_provincial_manager_cannot_resolve_existing_master(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_pending(world, material_raw="SKU-RESOLVE-MANAGER")
    material = _add_material(
        world,
        sku_code="SKU-RESOLVE-MANAGER",
        tracking_mode="none",
    )
    command = _command(
        prepared,
        disposition="resolved_existing_master",
        reason_code="existing_master_proved",
        comment="",
        material_id=material.id,
    )
    assert _error_code(
        lambda: _record(world, prepared, command=command)
    ) == "opening_observation_disposition_forbidden"


def test_admin_resolves_sku_using_existing_master_and_allows_only_post_cutoff_account(
    world: SimpleNamespace,
) -> None:
    raw = "SKU-RESOLVE-ADMIN"
    prepared = _prepare_pending(world, material_raw=raw)
    material = _add_material(world, sku_code=raw, tracking_mode="none")
    post_cutoff_account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=prepared.observation.owner_org_id,
        custodian_person_id=prepared.observation.custodian_person_id_snapshot,
        location_id=prepared.observation.location_id,
        material_id=material.id,
        condition_code=prepared.observation.condition_code,
        availability_bucket=prepared.observation.availability_bucket,
        lot_id=None,
        created_at=prepared.task.cutoff_at + timedelta(microseconds=1),
        updated_at=prepared.task.cutoff_at + timedelta(microseconds=1),
    )
    world.db.add(post_cutoff_account)
    world.db.flush()
    inventory_before = _inventory_counts(world.db)

    result = _record(
        world,
        prepared,
        actor_name="admin",
        command=_command(
            prepared,
            disposition="resolved_existing_master",
            reason_code="existing_master_proved",
            comment="",
            material_id=material.id,
        ),
    )

    assert result.resolved_material_id == material.id
    assert result.resolved_lot_id is None
    assert result.resolved_serial_id is None
    assert _inventory_counts(world.db) == inventory_before
    assert world.db.get(StockAccount, post_cutoff_account.id) is post_cutoff_account


def test_resolution_rejects_exact_dimension_account_at_cutoff(
    world: SimpleNamespace,
) -> None:
    raw = "SKU-CUTOFF-ACCOUNT"
    prepared = _prepare_pending(world, material_raw=raw)
    material = _add_material(world, sku_code=raw, tracking_mode="none")
    world.db.add(
        StockAccount(
            id=uuid.uuid4(),
            owner_org_id=prepared.observation.owner_org_id,
            custodian_person_id=prepared.observation.custodian_person_id_snapshot,
            location_id=prepared.observation.location_id,
            material_id=material.id,
            condition_code=prepared.observation.condition_code,
            availability_bucket=prepared.observation.availability_bucket,
            lot_id=None,
            created_at=prepared.task.cutoff_at,
            updated_at=prepared.task.cutoff_at,
        )
    )
    world.db.flush()
    assert _error_code(
        lambda: _record(
            world,
            prepared,
            actor_name="admin",
            command=_command(
                prepared,
                disposition="resolved_existing_master",
                reason_code="existing_master_proved",
                comment="",
                material_id=material.id,
            ),
        )
    ) == "opening_observation_disposition_cutoff_account_exists"


def test_admin_resolves_material_qr_lot_and_serial_qr_only_with_exact_active_mappings(
    world: SimpleNamespace,
) -> None:
    material_qr = "QR-MATERIAL-LOT-SERIAL-001"
    lot_no = "LOT-OPENING-001"
    serial_qr = "QR-SERIAL-OPENING-001"
    prepared = _prepare_pending(
        world,
        material_raw=material_qr,
        material_type="qr_code",
        lot_raw=lot_no,
        serial_raw=serial_qr,
        serial_type="qr_code",
        counted_qty=Decimal("1"),
    )
    material = _add_material(
        world,
        sku_code="SKU-QR-LOT-SERIAL-001",
        tracking_mode="lot_and_serial",
    )
    lot = InventoryLot(
        id=uuid.uuid4(),
        material_id=material.id,
        lot_no=lot_no,
        manufacture_date=None,
        expiry_date=None,
    )
    world.db.add(lot)
    world.db.flush()
    serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=material.id,
        serial_no="SN-OPENING-001",
        qr_code=serial_qr,
        lot_id=lot.id,
        lifecycle_status="active",
    )
    world.db.add(serial)
    world.db.flush()
    world.db.add_all(
        [
            QrCode(
                id=uuid.uuid4(),
                code=material_qr,
                object_type="material",
                object_id=material.id,
                status="active",
                printed_at=None,
            ),
            QrCode(
                id=uuid.uuid4(),
                code=serial_qr,
                object_type="serial",
                object_id=serial.id,
                status="active",
                printed_at=None,
            ),
        ]
    )
    world.db.flush()

    result = _record(
        world,
        prepared,
        actor_name="admin",
        command=_command(
            prepared,
            disposition="resolved_existing_master",
            reason_code="three_code_binding_proved",
            comment="",
            material_id=material.id,
            lot_id=lot.id,
            serial_id=serial.id,
        ),
    )
    assert (
        result.resolved_material_id,
        result.resolved_lot_id,
        result.resolved_serial_id,
    ) == (material.id, lot.id, serial.id)


def test_serial_qr_mapping_conflict_fails_closed(world: SimpleNamespace) -> None:
    raw = "SKU-SERIAL-QR-CONFLICT"
    serial_qr = "QR-SERIAL-CONFLICT"
    prepared = _prepare_pending(
        world,
        material_raw=raw,
        serial_raw=serial_qr,
        serial_type="qr_code",
        counted_qty=Decimal("1"),
    )
    material = _add_material(world, sku_code=raw, tracking_mode="serial")
    proven_serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=material.id,
        serial_no="SN-PROVEN",
        qr_code=serial_qr,
        lot_id=None,
        lifecycle_status="active",
    )
    other_serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=material.id,
        serial_no="SN-OTHER",
        qr_code="QR-SERIAL-OTHER",
        lot_id=None,
        lifecycle_status="active",
    )
    world.db.add_all([proven_serial, other_serial])
    world.db.flush()
    world.db.add(
        QrCode(
            id=uuid.uuid4(),
            code=serial_qr,
            object_type="serial",
            object_id=other_serial.id,
            status="active",
            printed_at=None,
        )
    )
    world.db.flush()

    assert _error_code(
        lambda: _record(
            world,
            prepared,
            actor_name="admin",
            command=_command(
                prepared,
                disposition="resolved_existing_master",
                reason_code="existing_master_proved",
                comment="",
                material_id=material.id,
                serial_id=proven_serial.id,
            ),
        )
    ) == "opening_observation_disposition_serial_qr_conflict"


def test_unknown_identifier_and_tracking_shape_never_resolve_by_guess(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_pending(
        world,
        material_raw="UNKNOWN-MATERIAL-CODE",
        material_type="unknown",
    )
    material = _add_material(
        world,
        sku_code="UNKNOWN-MATERIAL-CODE",
        tracking_mode="none",
    )
    assert _error_code(
        lambda: _record(
            world,
            prepared,
            actor_name="admin",
            command=_command(
                prepared,
                disposition="resolved_existing_master",
                reason_code="guess_forbidden",
                comment="",
                material_id=material.id,
            ),
        )
    ) == "opening_observation_disposition_material_unproven"


def test_cutoff_tracking_policy_requires_serial_dimension(
    world: SimpleNamespace,
) -> None:
    raw = "SKU-SERIAL-REQUIRED"
    prepared = _prepare_pending(world, material_raw=raw)
    material = _add_material(world, sku_code=raw, tracking_mode="serial")
    assert _error_code(
        lambda: _record(
            world,
            prepared,
            actor_name="admin",
            command=_command(
                prepared,
                disposition="resolved_existing_master",
                reason_code="tracking_incomplete",
                comment="",
                material_id=material.id,
            ),
        )
    ) == "opening_observation_disposition_tracking_policy_invalid"


def test_nonresolved_disposition_rejects_resolved_ids_and_blank_comment(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_pending(world)
    assert _error_code(
        lambda: _record(
            world,
            prepared,
            command=_command(
                prepared,
                disposition="pending_verification",
                comment="",
            ),
        )
    ) == "opening_observation_disposition_comment_required"
    assert _error_code(
        lambda: _record(
            world,
            prepared,
            command=_command(
                prepared,
                disposition="requires_recount",
                material_id=world.material.id,
            ),
        )
    ) == "opening_observation_disposition_unresolved_binding_invalid"


def test_same_key_same_request_is_read_only_replay_and_other_payload_conflicts(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_pending(world)
    first = _record(world, prepared)
    disposition_count = world.db.scalar(
        select(func.count()).select_from(StocktakeObservationDisposition)
    )
    audit_count = world.db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.action == "stocktake.opening.observation_disposed")
    )

    replay = _record(
        world,
        prepared,
        request_id="opening-disposition-request-retry-0002",
    )
    assert replay.disposition_id == first.disposition_id
    assert replay.replayed is True
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeObservationDisposition)
    ) == disposition_count
    assert world.db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.action == "stocktake.opening.observation_disposed")
    ) == audit_count

    assert _error_code(
        lambda: _record(
            world,
            prepared,
            command=replace(_command(prepared), comment="不同的处置请求"),
        )
    ) == "opening_observation_disposition_idempotency_conflict"
    assert _error_code(
        lambda: _record(
            world,
            prepared,
            key="opening-disposition-idempotency-other",
        )
    ) == "opening_observation_disposition_already_recorded"


def test_public_idempotent_replay_takes_one_audit_proof(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepare_pending(world)
    first = _record(world, prepared)
    original_lock = disposition_service._lock_audit_chain_head_with_proof
    proofs: list[object] = []

    def record_one_proof(*args, **kwargs):
        head, proof = original_lock(*args, **kwargs)
        proofs.append(proof)
        return head, proof

    monkeypatch.setattr(
        disposition_service,
        "_lock_audit_chain_head_with_proof",
        record_one_proof,
    )
    monkeypatch.setattr(
        disposition_service,
        "lock_audit_chain_head",
        lambda *_args, **_kwargs: pytest.fail(
            "public disposition replay must not take an ordinary audit lock"
        ),
        raising=False,
    )

    replay = _record(
        world,
        prepared,
        request_id="opening-disposition-single-proof-retry",
    )

    assert replay.disposition_id == first.disposition_id
    assert replay.replayed is True
    assert len(proofs) == 1


def test_replay_audit_verifier_plans_before_one_proof_then_stays_pure() -> None:
    capture_source = inspect.getsource(
        disposition_service._capture_audit_evidence_plan
    )
    assert "select(AuditEvent)" in capture_source
    assert "_lock_audit_chain_head_with_proof" not in capture_source
    assert "_verify_audit_event" not in capture_source
    assert ".with_for_update()" not in capture_source

    source = inspect.getsource(disposition_service._validate_audit_evidence)
    ordered = (
        "_capture_audit_evidence_plan",
        "_lock_audit_chain_head_with_proof",
        "_validate_opening_observation_disposition_replay_from_prelocked_audit_graph",
    )
    offsets = [source.index(marker) for marker in ordered]
    assert offsets == sorted(offsets)
    assert source.count("_lock_audit_chain_head_with_proof") == 1

    pure_source = inspect.getsource(
        disposition_service._validate_opening_observation_disposition_replay_from_prelocked_audit_graph
    )
    assert "_lock_audit_chain_head_with_proof" not in pure_source
    assert pure_source.count("_verify_audit_event_with_prelocked_proof") == 1
    assert "verify_audit_event_in_stream" not in pure_source
    assert ".with_for_update()" not in pure_source

    core_source = inspect.getsource(
        disposition_service._plan_opening_observation_disposition_replay
    )
    assert "select(RoleAssignment)" in core_source
    assert ".with_for_update()" not in core_source


def test_replay_audit_evidence_uses_one_proof_and_never_ordinary_verifier(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepare_pending(world)
    _record(world, prepared)
    row = world.db.scalar(select(StocktakeObservationDisposition))
    completion = world.db.scalar(select(StocktakeDifferenceSetCompletion))
    assert row is not None and completion is not None

    original_lock = disposition_service._lock_audit_chain_head_with_proof
    original_verify = disposition_service._verify_audit_event_with_prelocked_proof
    calls: list[str] = []

    def _lock_once(*args, **kwargs):
        calls.append("lock")
        return original_lock(*args, **kwargs)

    def _verify_with_proof(*args, **kwargs):
        calls.append("verify")
        return original_verify(*args, **kwargs)

    monkeypatch.setattr(
        disposition_service,
        "_lock_audit_chain_head_with_proof",
        _lock_once,
    )
    monkeypatch.setattr(
        disposition_service,
        "_verify_audit_event_with_prelocked_proof",
        _verify_with_proof,
    )
    monkeypatch.setattr(
        disposition_service,
        "lock_audit_chain_head",
        lambda *_args, **_kwargs: pytest.fail(
            "replay must not reacquire the audit head through the ordinary lock"
        ),
        raising=False,
    )
    monkeypatch.setattr(
        disposition_service,
        "verify_audit_event_in_stream",
        lambda *_args, **_kwargs: pytest.fail(
            "replay must not enter the ordinary audit verifier"
        ),
        raising=False,
    )

    disposition_service._validate_audit_evidence(world.db, row, completion)

    assert calls == ["lock", "verify"]


def test_stale_current_authorization_version_is_rejected(world: SimpleNamespace) -> None:
    prepared = _prepare_pending(world)
    world.manager_x.user.authorization_version += 1
    world.db.flush()
    assert _error_code(
        lambda: _record(world, prepared)
    ) == "opening_observation_disposition_actor_principal_stale"


def test_tampered_difference_completion_seal_is_rejected(world: SimpleNamespace) -> None:
    prepared = _prepare_pending(world)
    completion = world.db.scalar(select(StocktakeDifferenceSetCompletion))
    assert completion is not None
    completion.difference_manifest_sha256 = "0" * 64
    world.db.flush()
    assert _error_code(
        lambda: _record(world, prepared)
    ) == "opening_observation_disposition_count_evidence_invalid"


def test_tampered_disposition_manifest_breaks_read_only_replay(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_pending(world)
    first = _record(world, prepared)
    row = world.db.get(StocktakeObservationDisposition, first.disposition_id)
    assert row is not None
    row.disposition_manifest_sha256 = "0" * 64
    world.db.flush()
    assert _error_code(
        lambda: _record(world, prepared)
    ) == "opening_observation_disposition_replay_evidence_invalid"


def test_damaged_replay_task_anchor_fails_stably_before_resolution_dereference(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_pending(world)
    command = _command(prepared)
    first = _record(world, prepared, command=command)
    row = world.db.get(StocktakeObservationDisposition, first.disposition_id)
    difference = world.db.scalar(
        select(support.StocktakeDifference).where(
            support.StocktakeDifference.observed_line_id == prepared.observation.id
        )
    )
    submission = world.db.scalar(select(support.StocktakeRoundSubmission))
    completion = world.db.scalar(select(StocktakeDifferenceSetCompletion))
    assert row is not None and difference is not None
    assert submission is not None and completion is not None
    damaged_anchor = SimpleNamespace(
        request_sha256=row.request_sha256,
        task_id=uuid.uuid4(),
        round_id=row.round_id,
        scope_id=row.scope_id,
        observation_id=row.observation_id,
    )

    with pytest.raises(OpeningObservationDispositionError) as captured:
        disposition_service._validate_replay(
            world.db,
            actor=world.principals["manager_x"],
            command=command,
            row=damaged_anchor,
            task=prepared.task,
            observation=prepared.observation,
            difference=difference,
            submission=submission,
            difference_completion=completion,
            key_hash=row.idempotency_key_hash,
            request_sha256=row.request_sha256,
        )
    assert (
        captured.value.code
        == "opening_observation_disposition_replay_evidence_invalid"
    )


@pytest.mark.parametrize("downstream_kind", ["review", "posting", "establishment"])
def test_any_review_posting_or_establishment_blocks_late_disposition(
    world: SimpleNamespace,
    downstream_kind: str,
) -> None:
    prepared = _prepare_pending(world)
    digest_a = "a" * 64
    digest_b = "b" * 64
    regional = StocktakeReview(
        id=uuid.uuid4(),
        task_id=prepared.started.task_id,
        round_id=prepared.started.initial_round_id,
        review_stage="region",
        reviewer_user_id=world.manager_x.user.id,
        reviewer_person_id=world.manager_x.person.id,
        reviewer_role_assignment_id=world.manager_x.assignment.id,
        authorization_version=world.manager_x.user.authorization_version,
        decision="recount",
        comment="测试下游阻断",
        decision_manifest_sha256=digest_a,
        idempotency_key_hash=digest_a,
        reviewed_at=DISPOSITION_NOW,
        created_at=DISPOSITION_NOW,
    )
    posting = StocktakePosting(
        id=uuid.uuid4(),
        task_id=prepared.started.task_id,
        round_id=prepared.started.initial_round_id,
        posting_kind="opening",
        inventory_transaction_id=None,
        total_quantity=Decimal("0.000"),
        idempotency_key_hash=digest_b,
        request_hash=digest_b,
        posted_by_user_id=world.admin.user.id,
        posted_at=DISPOSITION_NOW,
        created_at=DISPOSITION_NOW,
    )
    if downstream_kind == "review":
        world.db.add(regional)
    elif downstream_kind == "posting":
        world.db.add(posting)
    else:
        headquarters = StocktakeReview(
            id=uuid.uuid4(),
            task_id=prepared.started.task_id,
            round_id=prepared.started.initial_round_id,
            review_stage="headquarters",
            reviewer_user_id=world.admin.user.id,
            reviewer_person_id=world.admin.person.id,
            reviewer_role_assignment_id=world.admin.assignment.id,
            authorization_version=world.admin.user.authorization_version,
            decision="recount",
            comment="测试下游阻断",
            decision_manifest_sha256=digest_b,
            idempotency_key_hash="c" * 64,
            reviewed_at=DISPOSITION_NOW,
            created_at=DISPOSITION_NOW,
        )
        world.db.add_all([regional, headquarters, posting])
        world.db.flush()
        world.db.add(
            InventoryOpeningEstablishment(
                id=uuid.uuid4(),
                task_id=prepared.started.task_id,
                scope_id=prepared.scope.id,
                owner_org_id=prepared.scope.owner_org_id,
                location_id=prepared.scope.location_id,
                round_id=prepared.started.initial_round_id,
                posting_id=posting.id,
                regional_review_id=regional.id,
                headquarters_review_id=headquarters.id,
                cutoff_ledger_cursor=prepared.task.cutoff_ledger_cursor,
                cutoff_at=prepared.task.cutoff_at,
                established_ledger_cursor=prepared.task.cutoff_ledger_cursor,
                scope_manifest_sha256=prepared.task.scope_manifest_sha256,
                snapshot_manifest_sha256=prepared.task.snapshot_manifest_sha256,
                count_manifest_sha256=(
                    world.db.get(
                        support.StocktakeRound,
                        prepared.started.initial_round_id,
                    ).count_manifest_sha256
                ),
                control_manifest_sha256=prepared.task.control_manifest_sha256,
                has_pending_control_difference=True,
                established_by_user_id=world.admin.user.id,
                established_at=DISPOSITION_NOW,
                created_at=DISPOSITION_NOW,
            )
        )
    world.db.flush()

    assert _error_code(
        lambda: _record(world, prepared)
    ) == "opening_observation_disposition_downstream_exists"


def test_resolution_never_updates_the_existing_master(world: SimpleNamespace) -> None:
    raw = "SKU-MASTER-UNCHANGED"
    prepared = _prepare_pending(world, material_raw=raw)
    material = _add_material(world, sku_code=raw, tracking_mode="none")
    external = world.db.get(ExternalObject, material.external_object_id)
    before = (
        material.sku_code,
        material.name,
        material.status,
        material.updated_at.replace(tzinfo=None),
        external.current_version_id,
    )
    _record(
        world,
        prepared,
        actor_name="admin",
        command=_command(
            prepared,
            disposition="resolved_existing_master",
            reason_code="existing_master_proved",
            comment="",
            material_id=material.id,
        ),
    )
    after = (
        material.sku_code,
        material.name,
        material.status,
        material.updated_at.replace(tzinfo=None),
        external.current_version_id,
    )
    assert after == before
