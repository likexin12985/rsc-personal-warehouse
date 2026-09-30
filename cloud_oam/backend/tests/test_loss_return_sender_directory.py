"""Own directory over real loss approval and derived-return evidence."""
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from app.inventory_models import CustodyAssignment
from app.formal_services import loss_return_sender_directory as query
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from test_stock_return_loss_origin import db, world, stock, allowed, evidence, regional, headquarters, approved, route, derived
from test_stock_loss_dispositions import snapshot


@pytest.fixture(autouse=True)
def independent_read_grant(derived, allowed):
    # The origin fixture grants physical execution only; directory reading is separate.
    derived.actor=replace(derived.actor,entitlements=derived.actor.entitlements+(
        replace(derived.actor.entitlements[-1],resource='stock_operation',action='read'),))
    allowed.world.current_principal=derived.actor


def test_directory_uses_requester_not_hq_author_and_keeps_complete_origin(db, derived, approved):
    before=snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    result=query.list_sender_returns(db,actor=derived.actor)
    assert result.person_id==derived.actor.person_id and len(result.items)==1
    item=result.items[0]
    assert item.origin.operation_id==derived.order.id and item.operation_no==derived.order.operation_no
    assert item.origin.requester_id==derived.actor.person_id
    assert item.origin.submitted_by_user_id==approved.actor.user_id!=derived.actor.user_id
    assert item.destination.source_location_id==derived.order.source_location_id
    assert result.next_after_id is None and len(result.snapshot_hash)==64
    wire=result.model_dump_json()
    assert all(key not in wire for key in ('qr_code','source_recovery_line_id','work_order_id','shipment_status','inbound_status'))
    assert snapshot(db)==before and not db.new and not db.dirty


def test_directory_keeps_history_after_custody_and_write_permission_end(db, derived, allowed):
    assignment=db.scalars(select(CustodyAssignment).where(CustodyAssignment.location_id==derived.order.source_location_id)).one()
    # End custody after derivation; subtracting a second can invalidate the
    # historical responsibility when the fixture finishes within that second.
    ended_at=datetime.now(timezone.utc)
    created_at=derived.order.created_at
    if created_at.tzinfo is None: created_at=created_at.replace(tzinfo=timezone.utc)
    assert created_at < ended_at
    assignment.valid_to=ended_at; db.commit()
    assert assignment.valid_to.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc)
    readonly=replace(derived.actor,entitlements=tuple(e for e in derived.actor.entitlements if e.action not in ('outbound_return','ship_return')))
    allowed.world.current_principal=readonly
    before=snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    assert query.list_sender_returns(db,actor=readonly).items[0].origin.operation_id==derived.order.id
    allowed.world.current_principal=replace(readonly,entitlements=tuple(e for e in readonly.entitlements if not(e.resource=='stock_operation' and e.action=='read')))
    with pytest.raises(InventoryReadError) as error: query.list_sender_returns(db,actor=readonly)
    assert error.value.code=='stock_return_forbidden' and snapshot(db)==before


def test_directory_never_lists_engineer_returns_for_hq_or_other_person(db, derived, approved, allowed):
    before=snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    reader=replace(approved.actor,entitlements=approved.actor.entitlements+(
        replace(approved.actor.entitlements[0],resource='stock_operation',action='read',field_code=''),))
    allowed.world.current_principal=reader
    assert query.list_sender_returns(db,actor=reader).items==()
    assert snapshot(db)==before


def test_directory_rejects_bad_cursor_and_stale_snapshot_without_writes(db, derived):
    before=snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    for change in ({'limit':0},{'limit':51},{'limit':True},{'after_id':str(uuid4())},
                   {'after_id':uuid4()},{'snapshot_hash':'bad'},{'snapshot_hash':[]}, {'snapshot_hash':'0'*64}):
        with pytest.raises(InventoryReadError): query.list_sender_returns(db,actor=derived.actor,**change)
    first=query.list_sender_returns(db,actor=derived.actor,limit=1)
    last=query.list_sender_returns(db,actor=derived.actor,limit=1,after_id=derived.order.id,snapshot_hash=first.snapshot_hash)
    assert last.items==() and last.next_after_id is None
    assert last.snapshot_hash==first.snapshot_hash and snapshot(db)==before


def test_directory_refuses_corrupt_origin_instead_of_skipping_it(db, derived):
    derived.order.reason+=' corrupted fixture'; db.commit()
    before=snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(InventoryReadError): query.list_sender_returns(db,actor=derived.actor)
    assert snapshot(db)==before


def test_directory_rechecks_audit_and_reader_during_read(db, derived, allowed, monkeypatch):
    before=snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    real=query.material_audit_cursor
    calls=0
    def changed(session):
        nonlocal calls
        calls+=1
        return real(session) if calls==1 else ()
    with monkeypatch.context() as patch:
        patch.setattr(query,'material_audit_cursor',changed)
        with pytest.raises(InventoryReadError) as error: query.list_sender_returns(db,actor=derived.actor)
        assert error.value.code=='stock_loss_return_directory_changed'
    authorize=query.authorize
    def revoke(session,actor,action):
        current=authorize(session,actor,action)
        allowed.world.current_principal=replace(derived.actor,entitlements=tuple(e for e in derived.actor.entitlements if not(e.resource=='stock_operation' and e.action=='read')))
        return current
    with monkeypatch.context() as patch:
        patch.setattr(query,'authorize',revoke)
        with pytest.raises((InventoryReadError,InventoryPostingError)): query.list_sender_returns(db,actor=derived.actor)
    assert snapshot(db)==before
