"""Actual history with explicit SQLite seal/audit seeds; no registrar proof."""
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, text
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, RolePermission
from app.return_condition_settlement_requests import ConditionSettlement
from app.formal_services.stock_loss_corrections import return_condition_decisions as decisions
from app.formal_services.stock_loss_corrections import return_condition_settlement as settlement
from app.formal_services.stock_loss_corrections import return_condition_settlement_recovery as recovery
from app.formal_services.stock_loss_corrections import return_condition_decision_seal_admission as admission
from app.formal_services.stock_loss_corrections import return_condition_history_read as history
from test_return_condition_decision_sealed_recovery import seed_reader_fixture
from test_return_condition_decisions import start, make_request
from test_return_condition_submission import snapshot
from test_return_condition_settlement import (
    db, initial_db, quantity_command, serial_command, authority_template, world, stock, allowed,
    evidence, regional, headquarters, approved, route, derived, ready, parcel, acceptance,
    prepared, regional_opening, reader_tables, context, regional_source, ERRORS, allow,
)


@pytest.mark.parametrize('stock,command_name', [('quantity','quantity_command'),
    ('serial','serial_command')], indirect=['stock'])
@pytest.mark.parametrize('action', ['execute','release'])
def test_closed_settlement_retains_old_input_after_stock_move_and_write_denial(
        db, regional_source, request, command_name, action):
    c = regional_source; original = request.getfixturevalue(command_name)
    result = start(db, c, original); db.commit()
    steps = [('verify_region',c.reviewer),('approve_hq',c.hq)] if action == 'execute' else [('withdraw',c.actor)]
    for kind, reviewer in steps:
        actor, command = make_request(db, reviewer, result, kind)
        result = decisions.decide(db, actor=actor, request=command); db.commit()
    missing = ConditionSettlement(action=action, case_id=UUID(result['case_id']),
        expected_event_id=UUID(result['event_id']), expected_event_hash='a'*64,
        reason='保留不确定结果的完整原扫码输入', serial_verifications=original.serial_verifications,
        request_id=uuid4().hex, idempotency_key=uuid4().hex)
    before = snapshot(db)
    prepared = admission.authorize_absence_seal(db, actor=c.actor, request=missing)
    assert not prepared.original_preflight_verified and snapshot(db) == before
    proof = history.read(db, actor=c.actor, inbound_line_id=c.line)
    row = seed_reader_fixture(db, c.actor, missing, proof)
    sealed = recovery.lookup(db, actor=c.actor, request=missing)
    assert sealed['request_state']=='sealed' and sealed['seal']['id']==str(row['id'])
    actual = missing.model_copy(update={'expected_event_hash':result['request_hash'],
        'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
    completed = settlement.settle(db, actor=c.actor, request=actual); db.commit()
    grant = db.scalar(select(RolePermission).join(Permission).where(
        RolePermission.role_id==c.regional_role.id, Permission.resource=='stock_operation',
        Permission.action==action+'_return_condition',Permission.field_code==''))
    assert grant is not None; grant.effect='deny'; db.commit()
    actor = load_formal_principal(db,c.actor.user_id)
    before = snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    try:
        old = recovery.lookup(db,actor=actor,request=missing)
        assert old['request_state']=='sealed' and old['seal']==sealed['seal']
        assert old['stock_effect']=='none' and not old['retry_allowed']
        assert not old['current_stock_verified'] and not old['original_preflight_verified']
        assert recovery.lookup(db,actor=actor,request=actual)['result']==completed
        changes = [{'reason':'changed original'}, {'expected_event_hash':'b'*64},
            {'idempotency_key':'different-original-key'}]
        if missing.serial_verifications:
            scans=list(missing.serial_verifications)
            scans[0]=scans[0].model_copy(update={'qr_code':'different-old-scan'})
            changes.append({'serial_verifications':tuple(scans)})
        for change in changes:
            with pytest.raises(ERRORS):
                recovery.lookup(db,actor=actor,request=missing.model_copy(update=change))
        assert snapshot(db)==before and not db.new and not db.dirty and not db.deleted
    finally:
        db.execute(text('PRAGMA query_only=OFF'))
    print(command_name+' '+action+' SEALED_AFTER_MOVE_AND_WRITE_DENIAL PASS',flush=True)
