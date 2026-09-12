"""Receipt evidence uses real upload completion, scope checks and DB bindings."""
from datetime import datetime, timezone
from pathlib import Path
import runpy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError

from app.formal_access import load_formal_principal
from app.formal_services import formal_files, material_request_receipt as legacy
from app.formal_services.material_request_query import MaterialRequestReadError
from app.foundation_models import Permission, RolePermission
from app.inventory_models import Receipt, ReceiptException, ReceiptLine, ReceiptSerial
from app.material_request_my_receipt_schemas import MyReceiptIn
from test_material_request_my_receipt import world, receiving_world, outbound_world, create, payload, evidence, facts
from test_material_request_draft_service import SECRET

pytest_plugins = ("test_material_request_picking",)
MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/20260926_0086_receipt_evidence_files.py"


def rejected(world, file_id):
    value = payload(world).model_dump()
    line = value["lines"][0]
    line.update(condition="rejected", rejected_qty=line["accepted_qty"], accepted_qty="0.000",
                rejected_serial_ids=line["accepted_serial_ids"], accepted_serial_ids=(),
                exception_evidence_file_id=file_id)
    return MyReceiptIn(**value)


def download(world, file, storage, actor=None):
    db, current, *_ = world
    return formal_files.create_file_download_intent(db, actor=actor or current, file_id=file.id,
        trace_request_id=f"download-{uuid4().hex}", storage=storage, download_ttl_seconds=60)


@pytest.mark.parametrize("change", ["pending", "wrong_purpose", "foreign_owner", "stale_identity"])
def test_exception_upload_requires_completed_exact_purpose_and_recipient(world, change):
    db, *_ = world
    file, _ = evidence(world, purpose="request_attachment" if change == "wrong_purpose" else "receipt_exception_evidence", complete=change != "pending")
    if change == "foreign_owner":
        file.metadata_jsonb = {**file.metadata_jsonb, "uploader_person_id": str(uuid4())}
    if change == "stale_identity":
        file.metadata_jsonb = {**file.metadata_jsonb, "authorization_version": file.metadata_jsonb["authorization_version"] + 1}
    db.flush()
    before = facts(db)
    with pytest.raises(MaterialRequestReadError, match="证据"):
        create(world, rejected(world, file.id))
    assert facts(db) == before


def test_recipient_and_fulfillment_operator_can_read_bound_evidence(world, outbound_world):
    db, actor, *_ = world
    file, storage = evidence(world)
    create(world, rejected(world, file.id))
    assert download(world, file, storage).purpose == "receipt_exception_evidence"
    assert download(world, file, storage, actor=outbound_world[1]).file_id == file.id
    permission = db.scalar(select(Permission).where(Permission.resource == "material_request", Permission.action == "receive"))
    db.query(RolePermission).filter(RolePermission.permission_id == permission.id).update({"effect": "deny"})
    db.flush()
    # Historical proof remains readable after write permission removal.
    assert download(world, file, storage, actor=load_formal_principal(db, actor.user_id)).file_id == file.id


@pytest.mark.parametrize("change", ["recipient", "custody", "inventory_scope"])
def test_download_revalidates_current_recipient_and_read_scope(world, change):
    db, _, _, location, shipment, _ = world
    file, storage = evidence(world)
    create(world, rejected(world, file.id))
    if change == "recipient":
        shipment.target_person_id = uuid4()
    elif change == "custody":
        location.status = "inactive"
    else:
        permission = db.scalar(select(Permission).where(Permission.resource == "inventory", Permission.action == "read"))
        db.query(RolePermission).filter(RolePermission.permission_id == permission.id).update({"effect": "deny"})
    db.flush()
    with pytest.raises(formal_files.FormalFileError):
        download(world, file, storage)
    assert storage.download_calls == []


def test_upload_permission_alone_does_not_grant_foreign_download(world, outbound_world):
    file, storage = evidence(world)
    with pytest.raises(formal_files.FormalFileError):
        download(world, file, storage, actor=outbound_world[1])
    assert not storage.download_calls


def test_evidence_binding_is_single_receipt_and_replay_does_not_rebind(world):
    from app.formal_services.material_request_my_receipt import _evidence
    file, _ = evidence(world)
    value = rejected(world, file.id)
    result = create(world, value)
    assert create(world, value).receipt_id == result.receipt_id
    with pytest.raises(MaterialRequestReadError, match="已绑定"):
        _evidence(world[0], world[1], value.lines[0])


def install_file_guards(db):
    migration = runpy.run_path(str(MIGRATION))
    with Operations.context(MigrationContext.configure(db.connection())):
        migration["_files"]()["_create_sqlite_triggers"]()
        migration["_sqlite"](True)
    return migration


def test_migrated_file_guards_allow_upload_binding_and_reject_direct_bad_inserts(world):
    db, actor, _, _, shipment, shipment_line = world
    migration = install_file_guards(db)
    file, storage = evidence(world)
    result = create(world, rejected(world, file.id))
    assert download(world, file, storage).file_id == file.id
    receipt = db.get(Receipt, result.receipt_id)
    line = db.scalar(select(ReceiptLine).where(ReceiptLine.receipt_id == receipt.id))
    for values in (
        {"evidence_file_id": None},
        {"evidence_file_id": uuid4()},
        {"receipt_line_id": uuid4()},
        {"exception_type": "damaged"},
    ):
        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(ReceiptException(**dict(id=uuid4(), receipt_id=receipt.id, receipt_line_id=line.id,
                exception_type=line.condition, detail="bad proof", evidence_file_id=file.id,
                created_at=datetime.now(timezone.utc)) | values))
            db.flush()
    other = Receipt(id=uuid4(), receipt_no=f"OTHER-{uuid4().hex}", shipment_id=shipment.id,
        receiver_person_id=actor.person_id, status="exception", received_at=datetime.now(timezone.utc),
        request_hash="c"*64, idempotency_key_hash="d"*64)
    db.add(other); db.flush()
    other_line = ReceiptLine(id=uuid4(), receipt_id=other.id, shipment_line_id=shipment_line.id,
        accepted_qty=0, rejected_qty=line.rejected_qty, condition=line.condition)
    db.add(other_line); db.flush()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(ReceiptException(id=uuid4(), receipt_id=other.id, receipt_line_id=other_line.id,
            exception_type=other_line.condition, detail="reused proof", evidence_file_id=file.id,
            created_at=datetime.now(timezone.utc)))
        db.flush()
    with Operations.context(MigrationContext.configure(db.connection())), pytest.raises(RuntimeError, match="0086 downgrade blocked"):
        migration["downgrade"]()


def legacy_create(world, source_actor, value):
    db, actor, request, *_ = world
    lines = tuple(SimpleNamespace(shipment_line_id=x.shipment_line_id, accepted_qty=x.accepted_qty,
        rejected_qty=x.rejected_qty, condition=x.condition,
        serial_ids=x.accepted_serial_ids + x.rejected_serial_ids,
        exception_evidence_file_id=x.exception_evidence_file_id) for x in value.lines)
    return legacy.create_receipt(db, actor=source_actor, request_id=request.id,
        expected_version=request.version, receiver_person_id=actor.person_id,
        received_at=value.received_at, lines=lines, idempotency_key="legacy-receipt-evidence-test-001",
        secret=SECRET, trace_request_id="legacy-receipt-evidence-trace-001")


def test_legacy_receipt_rejects_abnormal_without_proof(world, outbound_world):
    value = payload(world)
    line = value.lines[0].model_copy(update={"condition": "shortage"})
    with pytest.raises(legacy.ReceiptError, match="证据文件"):
        legacy_create(world, outbound_world[1], value.model_copy(update={"lines": (line,)}))


def test_legacy_rejected_serials_remain_rejected_with_migrated_file_guards(world, outbound_world):
    install_file_guards(world[0])
    file, storage = evidence(world)
    result = legacy_create(world, outbound_world[1], rejected(world, file.id))
    assert result["status"] == "exception"
    serials = world[0].scalars(select(ReceiptSerial)).all()
    assert all(not serial.accepted for serial in serials)
    assert download(world, file, storage).file_id == file.id


def test_normal_receipt_cannot_silently_drop_supplied_exception_file(world, outbound_world):
    file, _ = evidence(world)
    value = payload(world).model_dump()
    value["lines"][0]["exception_evidence_file_id"] = file.id
    with pytest.raises(ValidationError, match="正常验收"):
        MyReceiptIn(**value)
    normal = payload(world)
    changed = normal.model_copy(update={"lines": (normal.lines[0].model_copy(update={"exception_evidence_file_id": file.id}),)})
    with pytest.raises(legacy.ReceiptError, match="正常验收"):
        legacy_create(world, outbound_world[1], changed)


def test_legacy_receipt_checks_bound_recipient(world, outbound_world):
    world[4].target_person_id = uuid4()
    world[0].flush()
    with pytest.raises(legacy.ReceiptError, match="收件人"):
        legacy_create(world, outbound_world[1], payload(world))


def test_legacy_mixed_serial_outcome_is_rejected_without_per_serial_results(world, outbound_world):
    file, _ = evidence(world)
    value = rejected(world, file.id)
    line = value.lines[0]
    if not line.rejected_serial_ids:
        return
    half = line.rejected_qty / 2
    mixed = line.model_copy(update={"condition": "shortage", "accepted_qty": half, "rejected_qty": half})
    with pytest.raises(legacy.ReceiptError, match="分别登记"):
        legacy_create(world, outbound_world[1], value.model_copy(update={"lines": (mixed,)}))


def test_migration_failure_restores_original_file_guards(world, monkeypatch):
    db = world[0]
    migration = runpy.run_path(str(MIGRATION))
    with Operations.context(MigrationContext.configure(db.connection())):
        migration["_files"]()["_create_sqlite_triggers"]()
    db.commit()
    execute = migration["op"].execute
    def fail_last_trigger(sql, *args, **kwargs):
        if str(sql).startswith(f"CREATE TRIGGER {migration['TRIGGER']}"):
            raise RuntimeError("injected DDL failure")
        return execute(sql, *args, **kwargs)
    monkeypatch.setattr(migration["op"], "execute", fail_last_trigger)
    with Operations.context(MigrationContext.configure(db.connection())), pytest.raises(RuntimeError, match="injected DDL"):
        migration["upgrade"]()
    db.rollback()
    triggers = db.execute(text("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'" )).all()
    assert migration["TRIGGER"] not in {row.name for row in triggers}
    assert all("receipt_exception_evidence" not in row.sql for row in triggers)
    assert any(row.name == "trg_files_formal_insert_0036" for row in triggers)


def test_unbound_upload_intent_blocks_downgrade(world):
    db = world[0]
    migration = install_file_guards(db)
    evidence(world, complete=False)
    with Operations.context(MigrationContext.configure(db.connection())), pytest.raises(RuntimeError, match="0086 downgrade blocked"):
        migration["downgrade"]()
