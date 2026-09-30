"""Candidate exact-recovery contracts over actual synthetic return commands."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text
from app.formal_services import loss_return_sender_recovery as recovery
from app.formal_services.inventory_query import InventoryReadError
from test_stock_loss_return_shipment import db, world, stock, allowed, evidence, regional, headquarters, approved, route, derived, ready, parcel
from test_stock_loss_return_shipment import execute, snapshot
from test_stock_loss_return_outbound import submit


@pytest.fixture(params=['outbound_return', 'ship_return'])
def original(request, db, ready, allowed):
    if request.param == 'outbound_return':
        actor = ready.derived.actor
        actor = replace(actor, entitlements=actor.entitlements + (
            replace(actor.entitlements[-1], resource='stock_operation', action='read'),))
        ready.derived.actor = actor
        allowed.world.current_principal = actor
        result, command = submit(db, ready)
        order = ready.derived.order
    else:
        package = request.getfixturevalue('parcel')
        result, command = execute(db, package)
        actor, order = package.actor, package.order
    return SimpleNamespace(actor=actor, result=result, command=command, order=order, kind=request.param)


def lookup(db, original, command=None, operation_id=None):
    return recovery.lookup_sender_request(db, actor=original.actor, operation_type=original.kind,
        operation_id=operation_id or original.order.id, request=command or original.command)


def test_exact_found_works_with_read_permission_after_physical_grants_end(db, original, allowed):
    readonly = replace(original.actor, entitlements=tuple(e for e in original.actor.entitlements
        if e.action not in ('outbound_return', 'ship_return')))
    allowed.world.current_principal = readonly
    before = snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    result = lookup(db, original)
    assert result == {'lookup_status': 'found', 'retry_allowed': False,
        'operation_type': original.kind, 'result': original.result.model_dump(mode='json')}
    assert 'qr_code' not in str(result) and 'work_order_id' not in result['result']
    allowed.world.current_principal = replace(readonly, entitlements=tuple(e for e in readonly.entitlements
        if not (e.resource == 'stock_operation' and e.action == 'read')))
    with pytest.raises(InventoryReadError) as error: lookup(db, original)
    assert error.value.status_code == 403
    assert snapshot(db) == before and not db.new and not db.dirty


def test_changed_original_coordinates_never_resolve_as_success(db, original):
    before = snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    for update in ({'request_id': uuid4().hex}, {'idempotency_key': uuid4().hex},
                   {'expected_plan_hash': '0'*64}, {'reason': 'Different original request'},
                   {'operator_person_id': uuid4()}):
        with pytest.raises(InventoryReadError):
            lookup(db, original, original.command.model_copy(update=update))
    with pytest.raises(InventoryReadError) as error:
        lookup(db, original, operation_id=uuid4())
    assert error.value.status_code == 404
    assert snapshot(db) == before


def test_clean_absence_is_not_retry_permission_and_changed_cursor_is_rejected(db, original, monkeypatch):
    request = original.command.model_copy(update={'request_id': uuid4().hex, 'idempotency_key': uuid4().hex})
    before = snapshot(db); db.execute(text('PRAGMA query_only=ON'))
    assert lookup(db, original, request) == {'lookup_status': 'not_observed', 'retry_allowed': False}
    actual = recovery._cursor; calls = 0
    def changed(session):
        nonlocal calls
        calls += 1
        value = actual(session)
        return value if calls == 1 else (value[0]+1, value[1])
    monkeypatch.setattr(recovery, '_cursor', changed)
    with pytest.raises(InventoryReadError) as error: lookup(db, original, request)
    assert error.value.code == 'loss_return_sender_lookup_changed'
    assert snapshot(db) == before


def test_seal_is_audited_stock_neutral_idempotent_and_blocks_late_execution(db, original, allowed):
    from app.formal_services import loss_return_sender_seals as seals
    from app.formal_services import stock_return_outbound_commands as departures, stock_return_shipment_commands as parcels
    from app.stock_operation_models import StockOperationCommandSeal
    from app.foundation_models import AuditEvent
    from test_stock_return_shipment import inventory
    from sqlalchemy import select
    request = original.command.model_copy(update={'request_id': uuid4().hex, 'idempotency_key': uuid4().hex})
    before_stock = inventory(db)
    before_seals = tuple(db.scalars(select(StockOperationCommandSeal.id)))
    before_audits = tuple(db.scalars(select(AuditEvent.id)))
    result = seals.seal_sender_request(db, actor=original.actor, operation_type=original.kind,
        operation_id=original.order.id, request=request)
    db.commit()
    assert result['lookup_status'] == 'sealed' and result['retry_allowed'] is False
    assert result['seal']['seal_scope'] == 'actor_request_id'
    assert all(key not in result['seal'] for key in ('idempotency_key', 'expected_plan_hash', 'qr_code', 'work_order_id'))
    assert len(tuple(db.scalars(select(StockOperationCommandSeal.id)))) == len(before_seals)+1
    assert len(tuple(db.scalars(select(AuditEvent.id)))) == len(before_audits)+1
    assert inventory(db) == before_stock
    before = snapshot(db)
    assert seals.seal_sender_request(db, actor=original.actor, operation_type=original.kind,
        operation_id=original.order.id, request=request) == result
    db.commit(); assert snapshot(db) == before
    service = departures.execute_outbound if original.kind == 'outbound_return' else parcels.execute_shipment
    with pytest.raises(InventoryReadError) as error:
        service(db, actor=original.actor, work_order_id=None, operation_id=original.order.id, request=request)
    assert error.value.code == 'stock_return_request_sealed'
    db.rollback(); assert snapshot(db) == before
    allowed.world.current_principal = replace(original.actor, entitlements=tuple(e for e in original.actor.entitlements
        if e.action not in ('outbound_return', 'ship_return')))
    db.execute(text('PRAGMA query_only=ON'))
    assert lookup(db, original, request) == result
    # The tombstone closes this request identity even with a different unused
    # key; its response deliberately never certifies that key or plan digest.
    assert lookup(db, original, request.model_copy(update={'idempotency_key':uuid4().hex})) == result
    with pytest.raises(InventoryReadError) as error:
        lookup(db, original, request.model_copy(update={'reason':'Different sealed content'}))
    assert error.value.code == 'loss_return_sender_seal_conflict'
    assert inventory(db) == before_stock and snapshot(db) == before


def test_sealing_an_already_executed_original_returns_verified_fact_without_new_records(db, original):
    from app.formal_services import loss_return_sender_seals as seals
    before = snapshot(db)
    assert seals.seal_sender_request(db, actor=original.actor, operation_type=original.kind,
        operation_id=original.order.id, request=original.command) == lookup(db, original)
    db.commit(); assert snapshot(db) == before
