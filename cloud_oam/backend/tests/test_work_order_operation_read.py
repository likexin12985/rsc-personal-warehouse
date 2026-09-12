"""An unknown HTTP outcome is recovered only from original immutable facts."""
from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.database import get_db
from app.demand_models import WorkOrderMaterialLine
from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent
from app.formal_services import work_order_material as material
from app.formal_services import work_order_operation_read as recovery
from app.inventory_models import InventoryMovement
from app.routers import formal_work_order_material as api
from test_work_order_material_options import db, world, stock


def execute(db, stock, kind, *, trace=None):
    trace = trace or 'request-' + uuid4().hex
    if kind == 'occupy':
        line = stock.line('1', stock.serials[3:4])
    else:
        line = stock.line('1', stock.serials[1:2], identifier=stock.reserved.id,
                          target=stock.account.id if kind=='release' else None)
    operation, _ = getattr(material, f'execute_{kind}_operation')(db, actor=stock.actor,
        work_order_id=stock.orders[0].id, lines=(line,), idempotency_key=uuid4().hex, request_id=trace)
    db.commit()
    return operation, line, trace


def lookup(db, stock, kind, trace, **kwargs):
    return recovery.lookup_operation(db, actor=stock.world.current_principal, work_order_id=stock.orders[0].id,
        operation_type=kind, request_id=trace, **kwargs)


@pytest.mark.parametrize('kind', ['occupy', 'consume', 'release'])
def test_exact_request_proves_original_operation_without_writes(db, stock, kind):
    operation, line, trace = execute(db, stock, kind)
    db.execute(text('PRAGMA query_only=ON'))
    result = lookup(db, stock, kind, trace)
    assert result.lookup_status=='confirmed' and result.command.operation_id==operation.id
    assert result.command.request_id==trace and result.command.operator_person_id==stock.actor.person_id
    assert result.command.request_hash==recovery.client_request_hash(operation_type=kind, work_order_id=stock.orders[0].id,
        operator_person_id=stock.actor.person_id, lines=(line,))
    assert not db.new and not db.dirty and not db.deleted


def test_no_observation_or_other_order_is_not_confirmation(db, stock):
    _, _, trace = execute(db, stock, 'consume')
    assert lookup(db, stock, 'consume', 'never-observed').model_dump()['command'] is None
    assert lookup(db, stock, 'release', trace).lookup_status=='not_observed'
    other = recovery.lookup_operation(db, actor=stock.actor, work_order_id=stock.orders[1].id,
        operation_type='consume', request_id=trace)
    assert other.lookup_status=='not_observed' and other.command is None
    stock.world.current_principal = replace(stock.actor, user_id=str(uuid4()), person_id=stock.world.headquarters_reviewer_person.id,
        entitlements=tuple(replace(row, scope_type='national', scope_id='*') for row in stock.actor.entitlements))
    assert lookup(db, stock, 'consume', trace).lookup_status=='not_observed'


def test_original_occupancy_survives_later_consumption_closure_and_authorization_version_change(db, stock):
    operation, line, trace = execute(db, stock, 'occupy')
    material.execute_consume_operation(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        lines=(replace(line, stock_account_id=stock.reserved.id),), idempotency_key=uuid4().hex, request_id=uuid4().hex)
    stock.orders[0].status='closed'; db.commit()
    stock.world.current_principal = replace(stock.actor, authorization_version=stock.actor.authorization_version+1)
    assert lookup(db, stock, 'occupy', trace).command.operation_id==operation.id


@pytest.mark.parametrize('damage', ['operation_line', 'movement', 'command', 'inventory_audit', 'operation_audit', 'inventory_outbox', 'operation_outbox', 'state'])
def test_missing_or_changed_half_of_request_evidence_never_confirms(db, stock, damage):
    operation, _, trace = execute(db, stock, 'consume')
    if damage=='operation_line':
        db.scalar(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id==operation.id)).quantity += Decimal('0.001')
    elif damage=='movement':
        db.scalar(select(InventoryMovement).where(InventoryMovement.transaction_id==operation.posting_transaction_id)).external_boundary_code='wrong'
    elif damage=='command':
        event = db.scalar(select(AuditEvent).where(AuditEvent.aggregate_id==str(operation.id)))
        event.after_jsonb={**event.after_jsonb, 'line_count':99}
    else:
        aggregate = str(operation.posting_transaction_id) if damage.startswith('inventory') or damage=='state' else str(operation.id)
        model = StateTransitionEvent if damage=='state' else AuditEvent if damage.endswith('audit') else OutboxEvent
        record = db.scalar(select(model).where(model.aggregate_id==aggregate))
        if model is AuditEvent:
            # Preserve the audit-head foreign key; remove the exact
            # operation/transaction binding instead of disabling any FK.
            record.aggregate_id = str(uuid4())
        else:
            db.delete(record)
    db.commit()
    with pytest.raises(material.InventoryPostingError) as exc:
        lookup(db, stock, 'consume', trace)
    assert exc.value.code=='work_order_recovery_evidence_invalid'


def test_reused_request_with_multiple_matching_operations_is_ambiguous(db, stock):
    trace = 'same-request-' + uuid4().hex
    execute(db, stock, 'occupy', trace=trace)
    line = stock.line('1', stock.serials[4:5])
    material.execute_occupy_operation(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        lines=(line,), idempotency_key=uuid4().hex, request_id=trace)
    db.commit()
    with pytest.raises(material.InventoryPostingError) as exc:
        lookup(db, stock, 'occupy', trace)
    assert exc.value.code=='work_order_request_ambiguous'


def test_authority_revocation_during_evidence_read_discards_result(db, stock, monkeypatch):
    _, _, trace = execute(db, stock, 'consume')
    original = recovery._verified_result
    def revoke(*args, **kwargs):
        result = original(*args, **kwargs)
        stock.world.current_principal = replace(stock.actor, authorization_version=stock.actor.authorization_version+1)
        return result
    monkeypatch.setattr(recovery, '_verified_result', revoke)
    with pytest.raises(material.InventoryPostingError) as exc:
        recovery.lookup_operation(db, actor=stock.actor, work_order_id=stock.orders[0].id, operation_type='consume', request_id=trace)
    assert exc.value.code=='actor_principal_stale'


def test_http_original_request_lookup_has_no_store_and_never_replays(db, stock):
    operation, _, trace = execute(db, stock, 'consume')
    app = FastAPI(); app.include_router(api.router, prefix='/api')
    app.dependency_overrides[get_db]=lambda:db
    for route in api.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name=='principal': app.dependency_overrides[dependency.call]=lambda:stock.actor
    with TestClient(app) as client:
        response=client.get(f'/api/v1/work-orders/{stock.orders[0].id}/material-operations/consume/by-request/{trace}')
        assert response.status_code==200,response.text
        assert response.headers['cache-control']=='private, no-store'
        assert response.json()['command']['operation_id']==str(operation.id)
        response=client.get(f'/api/v1/work-orders/{stock.orders[0].id}/material-operations/consume/by-request/not-observed')
        assert response.status_code==200 and response.json()['lookup_status']=='not_observed'
