"""Real service postings and SQLite query-only historical recovery.

Bindings below are explicit SQLite fixture setup, never proof of the native
registration function or PostgreSQL permission/COMMIT guards.
"""
import hashlib
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, text
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, RolePermission, OutboxEvent
from app.stock_loss_correction_models import stock_loss_request_key_bindings as bindings
from app.formal_services import stock_loss_disposition_recovery as recovery
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import correction_approval, correction_execution, correction_stock
from app.formal_services.stock_loss_corrections.correction_models import StockLossCorrectionDecision as Decision, StockLossCorrectionExecution as Execution
from app.formal_services.stock_loss_corrections.request_contracts import CorrectionPreview, CorrectionExecute
from app.formal_services.stock_loss_corrections.sealed_inverse import _keys
from test_stock_loss_correction_approval import db, world, stock, allowed, evidence, regional, headquarters, approved, route, execution, prepared, recoverable, inverse_ready, request as approval_request
from test_stock_loss_correction_execution import snapshot
from app.formal_services.stock_loss_corrections import original_recovery as history
pytestmark = pytest.mark.parametrize('execution', ['restore_available'], indirect=True)

def bind_fixture(db, row, request, kind):
    keys = _keys(request)
    selected = {'inverse': 'inverse_id', 'approval': 'approval_id', 'correction': 'correction_id'}[kind]
    value = dict(fact_id=row.id, binding_kind=kind, root_disposition_id=row.root_disposition_id, actor_user_id=row.actor_user_id, request_id=row.request_id, request_hash=row.request_hash, key_token=hashlib.sha256(('cloud_oam.loss.correction.key.v1\x00' + request.idempotency_key).encode()).hexdigest(), reversal_key_hash=keys[0], approval_key_hash=keys[1], correction_key_hash=keys[2], created_at=row.created_at)
    value.update({column: row.id if column == selected else None for column in ('inverse_id', 'approval_id', 'correction_id', 'seal_id')})
    db.execute(bindings.insert().values(**value))
    db.commit()

def read(db, w, original, command=None):
    return recovery.lookup_disposition_request(db, actor=w.actor, request=command or original.command, flow=original.flow)

@pytest.mark.parametrize('kind', ['restore_available', 'convert_used', 'convert_damaged'])
def test_original_outcome_survives_inverse_approval_and_correction(db, inverse_ready, execution, kind, monkeypatch):
    w = inverse_ready
    expected = {'lookup_status': 'found', 'retry_permitted': False, 'disposition': recovery.facts.payload(w.root)}
    bind_fixture(db, w.inverse, w.command, 'inverse')
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    assert read(db, w, execution) == expected
    assert snapshot(db) == before and (not db.new) and (not db.dirty) and (not db.deleted)
    db.execute(text('PRAGMA query_only=OFF'))
    cmd = approval_request(w, kind)
    approved = correction_approval.approve(db, actor=w.actor, request=cmd)
    db.commit()
    decision = db.get(Decision, UUID(approved['correction_decision_id']))
    bind_fixture(db, decision, cmd, 'approval')
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    assert read(db, w, execution) == expected and snapshot(db) == before
    db.execute(text('PRAGMA query_only=OFF'))
    permission = Permission(resource='stock_operation', action='correct_loss', field_code='', description='Synthetic correction')
    db.add(permission)
    db.flush()
    db.add(RolePermission(role_id=w.admin_role.id, permission_id=permission.id, effect='allow'))
    db.commit()
    w.actor = load_formal_principal(db, w.actor.user_id)
    preview = CorrectionPreview(root_disposition_id=w.root.id, expected_root_request_hash=w.root.request_hash, expected_submission_plan_hash=w.order.plan_hash, reversal_id=w.inverse.id, expected_reversal_hash=w.inverse.request_hash, correction_decision_id=decision.id, expected_correction_decision_hash=decision.request_hash, reason='真实原处置恢复验证')
    plan = correction_stock.prepare(db, actor=w.actor, request=preview)
    cmd = CorrectionExecute(**preview.model_dump(), expected_plan_hash=plan.plan_hash, request_id=uuid4().hex, idempotency_key=uuid4().hex)
    posted = correction_execution.execute(db, actor=w.actor, request=cmd)
    db.commit()
    corrected = db.get(Execution, UUID(posted['correction_execution_id']))
    bind_fixture(db, corrected, cmd, 'correction')
    for grant in db.scalars(select(RolePermission).join(Permission, Permission.id == RolePermission.permission_id).where(RolePermission.role_id == w.admin_role.id, Permission.resource == 'stock_operation', Permission.action.in_(('dispose_loss', 'reverse_loss', 'approve_loss_correction', 'correct_loss')))):
        grant.effect = 'deny'
    db.commit()
    w.actor = load_formal_principal(db, w.actor.user_id)

    def forbidden(*args, **kwargs):
        raise AssertionError('Recovery cannot preview or post')
    monkeypatch.setattr(correction_execution, 'execute', forbidden)
    monkeypatch.setattr(correction_stock, 'prepare', forbidden)
    monkeypatch.setattr(recovery.disposition_plan, 'preview_disposition', forbidden)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    assert read(db, w, execution) == expected
    assert snapshot(db) == before and (not db.new) and (not db.dirty) and (not db.deleted)
    for field in ('request_id', 'idempotency_key', 'expected_plan_hash'):
        value = 'f' * 64 if field == 'expected_plan_hash' else uuid4().hex
        with pytest.raises(InventoryReadError):
            read(db, w, execution, execution.command.model_copy(update={field: value}))
    db.execute(text('PRAGMA query_only=OFF'))
    for fault in ('inverse_plan', 'correction_plan', 'event', 'binding_missing', 'binding_hash', 'binding_request'):
        point = db.begin_nested()
        try:
            if fault.endswith('_plan'):
                fact = w.inverse if fault == 'inverse_plan' else corrected
                fact.plan_jsonb = {**fact.plan_jsonb, 'source_balance_quantity': '999.000'}
                fact.plan_hash = recovery.sources._hash(fact.plan_jsonb)
            elif fault == 'event':
                event = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_type == 'stock_loss_correction_execution', OutboxEvent.aggregate_id == str(corrected.id)))
                db.delete(event)
            elif fault == 'binding_missing':
                db.execute(bindings.delete().where(bindings.c.fact_id == corrected.id))
            else:
                changes = {'correction_key_hash': 'f' * 64} if fault == 'binding_hash' else {'request_id': uuid4().hex}
                db.execute(bindings.update().where(bindings.c.fact_id == corrected.id).values(**changes))
            db.flush()
            altered = snapshot(db)
            db.execute(text('PRAGMA query_only=ON'))
            with pytest.raises(InventoryReadError) as caught:
                read(db, w, execution)
            assert caught.value.status_code == 503 and snapshot(db) == altered
        finally:
            db.execute(text('PRAGMA query_only=OFF'))
            point.rollback()
            db.expire_all()
    assert snapshot(db) == before
    actual = history._bound
    calls = 0

    def changed(session):
        nonlocal calls
        calls += 1
        value = actual(session)
        return value if calls == 1 else (value[0] + 1, value[1])
    monkeypatch.setattr(history, '_bound', changed)
    with pytest.raises(InventoryReadError) as caught:
        read(db, w, execution)
    assert caught.value.status_code == 503
    monkeypatch.setattr(history, '_bound', actual)
    grant = db.scalar(select(RolePermission).join(Permission, Permission.id == RolePermission.permission_id).where(RolePermission.role_id == w.admin_role.id, Permission.resource == 'stock_operation', Permission.action == 'read'))
    grant.effect = 'deny'
    db.commit()
    w.actor = load_formal_principal(db, w.actor.user_id)
    with pytest.raises(InventoryReadError) as caught:
        read(db, w, execution)
    assert caught.value.status_code == 403
