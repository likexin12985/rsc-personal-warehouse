"""Real service release -> independently prepared claim -> execute, quantity/SN.

SQLite uses explicit fixture storage/key test doubles; native COMMIT is a
separate gate. This regression targets the observed post-release rejection.
"""
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from sqlalchemy import func, select
from app.foundation_models import FileObject
from app.inventory_models import StockBalance, SerialCurrentPosition
from app.return_condition_settlement_requests import ConditionSettlement
from app.formal_services.stock_loss_corrections import return_condition_submission as submission
from app.formal_services.stock_loss_corrections import return_condition_submission_source as preparation
from app.formal_services.stock_loss_corrections import return_condition_decisions as decisions
from app.formal_services.stock_loss_corrections import return_condition_settlement as settlement
from app.formal_services.stock_loss_corrections import return_condition_settlement_recovery as recovery
from app.formal_services.stock_loss_corrections import return_condition_history_read as history
from test_return_condition_settlement import (
    db, initial_db, quantity_command, serial_command, authority_template, world, stock, allowed, evidence,
    regional, headquarters, approved, route, derived, ready, parcel, acceptance, prepared,
    regional_opening, reader_tables, context, regional_source, ERRORS, allow,
)
from test_return_condition_decisions import start, make_request
from test_return_condition_submission import snapshot
from test_return_condition_evidence import create, finish
from test_formal_files_service import FakeStorage


@pytest.mark.parametrize('stock,command_name', [('quantity', 'quantity_command'),
    ('serial', 'serial_command')], indirect=['stock'])
def test_released_share_can_be_newly_claimed_without_replaying_old_request(db, regional_source, request, command_name):
    c = regional_source
    command = request.getfixturevalue(command_name)
    original_total = db.scalar(select(func.sum(StockBalance.quantity)))
    first = start(db, c, command); db.commit()
    checked = preparation.inspect_submission_source(db, actor=c.actor, inbound_line_id=c.line)
    doc = checked.document
    assert doc['source_status'] == 'verified_condition_history'
    assert Decimal(doc['claimable_quantity']) == Decimal(doc['historical_damaged_quantity']) - command.quantity
    before = snapshot(db)
    excessive = command.model_copy(update=dict(quantity=Decimal(doc['historical_damaged_quantity']),
        expected_source_hash=checked.evidence_hash, request_id=uuid4().hex, idempotency_key=uuid4().hex))
    with pytest.raises(ERRORS, match='未占用的原破损份额'):
        submission.submit(db, actor=c.actor, request=excessive)
    db.rollback(); assert snapshot(db) == before
    actor, withdrawal = make_request(db, c.actor, first, 'withdraw')
    stopped = decisions.decide(db, actor=actor, request=withdrawal); db.commit()
    release_input = ConditionSettlement(action='release', case_id=UUID(stopped['case_id']),
        expected_event_id=UUID(stopped['event_id']), expected_event_hash=stopped['request_hash'],
        reason='已撤回申请的准确冻结份额释放', serial_verifications=command.serial_verifications,
        request_id=uuid4().hex, idempotency_key=uuid4().hex)
    released = settlement.settle(db, actor=c.actor, request=release_input); db.commit()
    assert released['status'] == 'released_cancelled'
    assert db.scalar(select(func.sum(StockBalance.quantity))) == original_total
    upload = SimpleNamespace(actor=c.actor, storage=FakeStorage())
    file = db.get(FileObject, create(db, upload).file_id)
    finish(db, upload, file); db.commit()
    checked = preparation.inspect_submission_source(db, actor=c.actor, inbound_line_id=c.line)
    doc = checked.document
    assert doc['source_status'] == 'verified_condition_history'
    assert doc['claimable_quantity'] == doc['historical_damaged_quantity']
    if command.serial_verifications:
        assert all(row['claimable_for_correction'] for row in doc['serials'])
        assert not any(row['retained_at_original_inbound'] for row in doc['serials'])
    fresh = command.model_copy(update=dict(quantity=Decimal(doc['claimable_quantity']),
        expected_source_hash=checked.evidence_hash, evidence_file_ids=(file.id,),
        reason='释放后重新完成物理核验的独立申请', request_id=uuid4().hex, idempotency_key=uuid4().hex))
    result = submission.submit(db, actor=c.actor, request=fresh); db.commit()
    assert result['case_id'] != first['case_id']
    assert recovery.lookup(db, actor=c.actor, request=release_input)['result'] == released
    for kind, reviewer in [('verify_region', c.reviewer), ('approve_hq', c.hq)]:
        actor, decision = make_request(db, reviewer, result, kind)
        result = decisions.decide(db, actor=actor, request=decision); db.commit()
    execute_input = ConditionSettlement(action='execute', case_id=UUID(result['case_id']),
        expected_event_id=UUID(result['event_id']), expected_event_hash=result['request_hash'],
        reason='新申请审批后转为坏件', serial_verifications=fresh.serial_verifications,
        request_id=uuid4().hex, idempotency_key=uuid4().hex)
    executed = settlement.settle(db, actor=c.actor, request=execute_input); db.commit()
    assert executed['status'] == 'executed'
    assert db.scalar(select(func.sum(StockBalance.quantity))) == original_total
    proved = history.read(db, actor=c.actor, inbound_line_id=c.line)
    assert proved.graph.projection.unclaimed_quantity == 0
    assert proved.graph.projection.held_quantity == 0
    assert proved.graph.projection.corrected_quantity == fresh.quantity
    assert recovery.lookup(db, actor=c.actor, request=release_input)['result'] == released
    assert recovery.lookup(db, actor=c.actor, request=execute_input)['result'] == executed
    print(command_name + ' RELEASE_NEW_CLAIM_EXECUTE_AND_OLD_LOOKUP PASS', flush=True)
