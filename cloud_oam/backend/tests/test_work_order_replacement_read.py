"""Historical replacement recovery cannot depend on current OAM assignment."""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text

from app.database import get_db
from app.demand_models import WorkOrderMaterialOperation
from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent
from app.formal_services import work_order_material as material
from app.formal_services import work_order_replacement_read as recovery
from app.formal_services import work_order_replacements as service
from app.routers import formal_work_order_material as router
from test_work_order_material_options import db, world, stock


@pytest.fixture
def replacement(db, stock):
    consumed = stock.line("1", stock.serials[1:2], identifier=stock.reserved.id)
    removed = stock.serials[:1]
    result = service.execute_replacement(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        consume_lines=(consumed,), recover_lines=(service.RecoveryLineInput(stock.reserved.id,
            stock.world.material.id, Decimal(1), "used", serial_ids=tuple(row.id for row in removed),
            serial_verifications=tuple(material.SerialVerificationInput(row.id, stock.world.material.sku_code,
                row.serial_no, row.qr_code) for row in removed)),),
        pairs=(material.WorkOrderReplacementPairInput(consumed.serial_ids[0], removed[0].id),) if removed else (),
        idempotency_key=uuid4().hex, request_id="replacement-read-"+uuid4().hex)
    db.commit()
    return result


def lookup(db, stock, replacement):
    return recovery.lookup_replacement(db, actor=stock.world.current_principal,
        work_order_id=replacement.oam_work_order_id, request_id=replacement.request_id)


def test_original_replacement_is_read_only_after_order_reassignment_closure_and_version_change(db, stock, replacement):
    stock.orders[0].engineer_person_id=stock.world.headquarters_reviewer_person.id
    stock.orders[0].status="closed"
    db.commit()
    stock.world.current_principal=replace(stock.actor, authorization_version=stock.actor.authorization_version+1)
    db.execute(text("PRAGMA query_only=ON"))
    result=lookup(db,stock,replacement)
    assert result.replacement_id==replacement.id
    assert result.operator_person_id==stock.actor.person_id
    assert result.request_id==replacement.request_id and result.request_hash==replacement.request_hash
    assert result.consume_transaction_id != result.recover_transaction_id
    assert not db.new and not db.dirty and not db.deleted


def test_absence_other_order_and_foreign_national_reader_do_not_reveal_original_command(db,stock,replacement):
    assert recovery.lookup_replacement(db,actor=stock.actor,work_order_id=stock.orders[1].id,request_id=replacement.request_id) is None
    assert recovery.lookup_replacement(db,actor=stock.actor,work_order_id=stock.orders[0].id,request_id="not-observed") is None
    stock.world.current_principal=replace(stock.actor,user_id=str(uuid4()),person_id=stock.world.headquarters_reviewer_person.id,
        entitlements=tuple(replace(row,scope_type="national",scope_id="*") for row in stock.actor.entitlements))
    assert lookup(db,stock,replacement) is None


@pytest.mark.parametrize("side",["consume","recover"])
@pytest.mark.parametrize("damage",["inventory_audit","inventory_outbox","state","operation_audit_chain"])
def test_either_transaction_missing_evidence_or_broken_audit_chain_cannot_confirm(db,stock,replacement,side,damage):
    operation=db.get(WorkOrderMaterialOperation,getattr(replacement,side+"_operation_id"))
    model={"inventory_audit":AuditEvent,"inventory_outbox":OutboxEvent,"state":StateTransitionEvent,
        "operation_audit_chain":AuditEvent}[damage]
    aggregate=str(operation.id if damage=="operation_audit_chain" else operation.posting_transaction_id)
    evidence=db.scalar(select(model).where(model.aggregate_id==aggregate))
    assert evidence is not None
    if damage=="operation_audit_chain": evidence.event_hash="0"*64
    elif damage=="inventory_audit": evidence.request_id="wrong-original-request"
    else: db.delete(evidence)
    db.commit()
    with pytest.raises(material.InventoryPostingError) as exc: lookup(db,stock,replacement)
    assert exc.value.code=="replacement_evidence_invalid"


@pytest.mark.parametrize("phase",["before","during"])
def test_permission_revocation_and_mid_read_principal_drift_stop_recovery(db,stock,replacement,monkeypatch,phase):
    if phase=="before":
        stock.world.current_principal=replace(stock.actor,entitlements=tuple(row for row in stock.actor.entitlements
            if not (row.resource=="work_order_material" and row.action=="read")))
    else:
        original=recovery.replacement_result
        def change(*args,**kwargs):
            result=original(*args,**kwargs)
            stock.world.current_principal=replace(stock.actor,authorization_version=stock.actor.authorization_version+1)
            return result
        monkeypatch.setattr(recovery,"replacement_result",change)
    with pytest.raises(material.InventoryPostingError) as exc: lookup(db,stock,replacement)
    assert exc.value.code==("work_order_forbidden" if phase=="before" else "actor_principal_stale")


def test_http_recovery_returns_exact_digest_after_reassignment_without_writes(db,stock,replacement):
    stock.orders[0].engineer_person_id=stock.world.headquarters_reviewer_person.id
    db.commit(); db.execute(text("PRAGMA query_only=ON"))
    app=FastAPI();app.include_router(router.router,prefix="/api")
    app.dependency_overrides[get_db]=lambda:db
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name=="principal":app.dependency_overrides[dependency.call]=lambda:stock.actor
    with TestClient(app) as client:
        response=client.get(f"/api/v1/work-orders/{replacement.oam_work_order_id}/material-replacements/by-request/{replacement.request_id}")
        assert response.status_code==200,response.text
        assert response.headers["cache-control"]=="private, no-store"
        assert response.json()["request_hash"]==replacement.request_hash
        assert response.json()["operator_person_id"]==str(stock.actor.person_id)
        assert "qr_code" not in response.text and "command_jsonb" not in response.text
