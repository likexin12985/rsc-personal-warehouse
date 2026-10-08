"""Complete history from real source and submit services, without current write authority."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update, delete, text

from app.formal_access import load_formal_principal
from app.inventory_models import CustodyAssignment
from app.foundation_models import Permission, RolePermission, OutboxEvent, StateTransitionEvent
from app.formal_services import inventory_posting as posting
from app.formal_services.stock_loss_corrections import return_condition_submission as writer
from app.formal_services.stock_loss_corrections import return_condition_history as graph
from app.formal_services.stock_loss_corrections import return_condition_history_read as reader
from app.formal_services.inventory_query import InventoryReadError
from formal_file_integrity import FormalFileError
from test_return_condition_ledger_facts import db,quantity_command,serial_command,authority_template
from test_return_condition_submission import snapshot
from test_return_condition_submission_source import (
    world,stock,allowed,evidence,regional,headquarters,approved,route,derived,ready,parcel,
    acceptance,prepared,regional_opening,reader_tables, context,regional_source,ERRORS,
)


@pytest.mark.parametrize('stock,command_name',[('quantity','quantity_command'),('serial','serial_command')],indirect=['stock'])
def test_complete_scoped_history_and_corruption_refusal(db,regional_source,request,command_name,monkeypatch):
    c=regional_source;command=request.getfixturevalue(command_name)
    before=snapshot(db)
    empty=reader.read(db,actor=c.actor,inbound_line_id=c.line)
    assert empty.graph.event_ids==() and empty.retry_allowed is False and empty.current_stock_verified is False
    assert snapshot(db)==before
    db.rollback()
    result=writer.submit(db,actor=c.actor,request=command);db.commit()
    before=snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    history=reader.read(db,actor=c.actor,inbound_line_id=c.line)
    db.execute(text('PRAGMA query_only=OFF'))
    assert snapshot(db)==before
    assert history.graph.event_ids==(UUID(result['event_id']),)
    assert history.graph.projection.held_quantity==command.quantity
    assert history.graph.projection.corrected_quantity==0
    assert history.graph.projection.unclaimed_quantity==history.basis.affected.quantity-command.quantity
    assert history.graph.projection.cases[0].status=='awaiting_regional'
    assert history.current_stock_verified is False and history.retry_allowed is False
    t=graph.tables();e=t['stock_condition_events'];cases=t['stock_condition_cases'];line=t['stock_operation_lines']
    order=t['stock_operation_orders'];files=t['stock_condition_files'];registry=t['stock_condition_submission_requests']
    event_id=UUID(result['event_id']);case_id=UUID(result['case_id'])
    edits=[
        update(e).where(e.c.id==event_id).values(event_sequence=2),
        update(e).where(e.c.id==event_id).values(plan_jsonb={'forged':True}),
        update(order).where(order.c.id==case_id).values(request_hash='0'*64),
        update(line).where(line.c.operation_id==case_id).values(reason='different immutable reason'),
        delete(registry).where(registry.c.event_id==event_id),
        delete(files).where(files.c.event_id==event_id),
        update(OutboxEvent).where(OutboxEvent.aggregate_type=='stock_condition_event',
            OutboxEvent.aggregate_id==str(event_id)).values(payload_jsonb={'forged':True}),
    ]
    if command_name=='quantity_command':
        edits.append(update(cases).where(cases.c.id==case_id).values(affected_quantity=Decimal('.376')))
    else:
        sn=t['stock_operation_serials']
        line_id=db.scalar(select(line.c.id).where(line.c.operation_id==case_id))
        edits.append(delete(sn).where(sn.c.line_id==line_id))
    persisted=db.execute(select(e).where(e.c.id==event_id)).mappings().one()
    forged_plan=dict(persisted['plan_jsonb'],quantity='999')
    forged_plan_hash=posting._canonical_hash(forged_plan)
    forged_command=dict(persisted['command_jsonb'],expected_plan_hash=forged_plan_hash)
    edits.append(update(e).where(e.c.id==event_id).values(plan_jsonb=forged_plan,plan_hash=forged_plan_hash,
        command_jsonb=forged_command,request_hash=posting._canonical_hash(forged_command)))
    for statement in edits:
        db.execute(statement);db.flush()
        with pytest.raises((*ERRORS,FormalFileError)):
            graph.verify(db,basis=history.basis)
        db.rollback();assert snapshot(db)==before
    # A correct effect must not hide a second contradictory one for the same event.
    for model in (OutboxEvent,StateTransitionEvent):
        table=model.__table__
        original=db.execute(select(table).where(table.c.aggregate_type=='stock_condition_event',
            table.c.aggregate_id==str(event_id))).mappings().one()
        db.execute(table.insert().values(**dict(original,id=uuid4(),idempotency_key='extra:'+uuid4().hex)))
        with pytest.raises(ERRORS):
            graph.verify(db,basis=history.basis)
        db.rollback();assert snapshot(db)==before
    other_line=uuid4()
    incoming=t['stock_operation_return_inbound_lines']
    original_line=db.execute(select(incoming).where(incoming.c.id==c.line)).mappings().one()
    db.execute(incoming.insert().values(**dict(original_line,id=other_line,line_no=999,condition_code='damaged')))
    case=db.execute(select(cases).where(cases.c.id==case_id)).mappings().one()
    altered_doc=dict(case['source_jsonb'],selection=dict(case['source_jsonb']['selection'],inbound_line_id=str(other_line)))
    db.execute(update(cases).where(cases.c.id==case_id).values(inbound_line_id=other_line,source_jsonb=altered_doc))
    db.execute(update(e).where(e.c.id==event_id).values(inbound_line_id=other_line))
    db.execute(update(order).where(order.c.id==case_id).values(plan_jsonb=dict(persisted['plan_jsonb'],inbound_line_id=str(other_line))))
    captured,_=graph.capture(db,c.line)
    assert any(r['id']==case_id for r in captured['stock_condition_cases'])
    with pytest.raises(ERRORS):
        graph.verify(db,basis=history.basis)
    db.rollback();assert snapshot(db)==before
    # Revoking the new-write permission must not erase an original outcome.
    grant=db.scalar(select(RolePermission).join(Permission).where(RolePermission.role_id==c.regional_role.id,
        Permission.resource=='stock_operation',Permission.action=='submit_return_condition'))
    grant.effect='deny';db.commit()
    current=load_formal_principal(db,c.actor.user_id)
    retained=reader.read(db,actor=current,inbound_line_id=c.line)
    assert retained.graph==history.graph
    db.rollback()
    # Closing today's custody does not change custody at the old submit time.
    db.execute(update(CustodyAssignment).where(CustodyAssignment.id==c.custody.id)
        .values(valid_to=datetime.now(timezone.utc)))
    db.commit()
    assert reader.read(db,actor=current,inbound_line_id=c.line).graph==history.graph
    db.rollback()
    # A view grant lost during the read must be caught by the final scope check.
    read_grant=db.scalar(select(RolePermission).join(Permission).where(RolePermission.role_id==c.regional_role.id,
        Permission.resource=='stock_operation',Permission.action=='read'))
    real_verify=graph.verify
    def revoke_after_proof(*args,**kwargs):
        value=real_verify(*args,**kwargs)
        db.execute(update(RolePermission).where(RolePermission.id==read_grant.id).values(effect='deny'))
        return value
    with monkeypatch.context() as raced:
        raced.setattr(graph,'verify',revoke_after_proof)
        with pytest.raises(InventoryReadError) as error:
            reader.read(db,actor=current,inbound_line_id=c.line)
        assert error.value.code=='return_condition_history_forbidden'
    db.rollback()
    # Current viewing rights are checked before the expensive history proof.
    grant=db.scalar(select(RolePermission).join(Permission).where(RolePermission.role_id==c.regional_role.id,
        Permission.resource=='stock_operation',Permission.action=='read'))
    grant.effect='deny';db.commit()
    current=load_formal_principal(db,c.actor.user_id)
    def forbidden_proof(*args,**kwargs):
        raise AssertionError('history must not be exposed before scope admission')
    monkeypatch.setattr(graph,'verify',forbidden_proof)
    with pytest.raises(InventoryReadError) as error:
        reader.read(db,actor=current,inbound_line_id=c.line)
    assert error.value.code=='return_condition_history_forbidden'
