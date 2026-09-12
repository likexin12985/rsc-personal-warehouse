"""A sealed original request can never become another inventory command."""
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.demand_models import WorkOrderCommandSeal
from app.foundation_models import AuditEvent
from app.formal_services import work_order_command_seal as seals
from app.formal_services import work_order_material as material
from app.formal_services.work_order_operation_read import client_request_hash
from app.inventory_models import InventoryTransaction
from test_work_order_material_options import db, world, stock
from test_work_order_operation_read import execute
from test_work_order_current_source import line


def args(stock, kind='occupy', trace=None):
    return dict(actor=stock.actor, work_order_id=stock.orders[0].id, operation_type=kind,
        request_id=trace or uuid4().hex, request_hash=client_request_hash(operation_type=kind,
            work_order_id=stock.orders[0].id, operator_person_id=stock.actor.person_id, lines=(line(stock, kind),)))


@pytest.mark.parametrize('kind', ['occupy', 'consume', 'release'])
def test_seal_creates_no_stock_and_permanently_rejects_late_command_even_with_new_key(db, stock, kind):
    before = tuple(db.scalars(select(InventoryTransaction.id)))
    values = args(stock, kind)
    result = seals.seal_command(db, **values); db.commit()
    assert result.lookup_status == 'sealed_not_executed' and result.command is None
    assert tuple(db.scalars(select(InventoryTransaction.id))) == before
    for key in (uuid4().hex, uuid4().hex):
        with pytest.raises(material.InventoryPostingError) as exc:
            getattr(material, f'execute_{kind}_operation')(db, actor=stock.actor, work_order_id=stock.orders[0].id,
                request_id=values['request_id'], idempotency_key=key, lines=(line(stock, kind),))
        assert exc.value.code == 'work_order_request_sealed'
        db.rollback()
    assert seals.seal_command(db, **values).seal.seal_id == result.seal.seal_id
    db.rollback()
    db.execute(text('PRAGMA query_only=ON'))
    read = seals.lookup_command_result(db, **{k:v for k,v in values.items() if k!='request_hash'})
    assert read.seal.seal_id == result.seal.seal_id and read.seal.request_hash == values['request_hash']
    assert not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize('kind', ['occupy', 'consume', 'release'])
def test_executed_command_returns_original_proof_instead_of_seal(db, stock, kind):
    operation, submitted, trace = execute(db, stock, kind)
    values = args(stock, kind, trace)
    values['request_hash'] = client_request_hash(operation_type=kind, work_order_id=stock.orders[0].id,
        operator_person_id=stock.actor.person_id, lines=(submitted,))
    result = seals.seal_command(db, **values)
    assert result.lookup_status == 'confirmed' and result.command.operation_id == operation.id
    assert tuple(db.scalars(select(WorkOrderCommandSeal.id))) == ()
    db.rollback()
    with pytest.raises(material.InventoryPostingError) as exc:
        seals.seal_command(db, **{**values, 'request_hash': '0'*64})
    assert exc.value.code == 'work_order_seal_request_conflict'


def test_reassignment_closure_and_later_authority_version_do_not_remove_original_seal(db, stock):
    stock.orders[0].engineer_person_id = stock.world.headquarters_reviewer_person.id
    stock.orders[0].status = 'closed'; db.commit()
    values = args(stock)
    original = seals.seal_command(db, **values); db.commit()
    current = replace(stock.actor, authorization_version=stock.actor.authorization_version+1)
    stock.world.current_principal = current
    result = seals.seal_command(db, **{**values, 'actor': current})
    assert result.seal.seal_id == original.seal.seal_id


def test_seal_is_exact_to_order_kind_and_user_not_a_global_stock_block(db, stock):
    values = args(stock)
    seals.seal_command(db, **values); db.commit()
    for change in ({'operation_type': 'consume'}, {'work_order_id': stock.orders[1].id}, {'actor': replace(stock.actor, user_id=str(uuid4()))}):
        seals.require_unsealed_request(db, **{**{k:v for k,v in values.items() if k!='request_hash'}, **change})
    operation, _ = material.execute_occupy_operation(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        request_id=uuid4().hex, idempotency_key=uuid4().hex, lines=(line(stock,'occupy'),))
    assert operation.status == 'posted'; db.rollback()


def test_conflicting_digest_and_broken_audit_preserve_the_seal(db, stock):
    values = args(stock)
    result = seals.seal_command(db, **values); db.commit()
    with pytest.raises(material.InventoryPostingError) as exc:
        seals.seal_command(db, **{**values, 'request_hash':'0'*64})
    assert exc.value.code == 'work_order_seal_request_conflict'
    db.rollback()
    event = db.scalar(select(AuditEvent).where(AuditEvent.aggregate_id == str(result.seal.seal_id)))
    event.after_jsonb = {}; db.commit()
    with pytest.raises(material.InventoryPostingError) as exc:
        seals.lookup_command_result(db, **{k:v for k,v in values.items() if k!='request_hash'})
    assert exc.value.code == 'work_order_seal_evidence_invalid'
    assert db.get(WorkOrderCommandSeal, result.seal.seal_id) is not None


@pytest.mark.parametrize('action', ['read', 'operate'])
def test_sealing_requires_current_own_read_and_operate_permissions(db, stock, action):
    actor = replace(stock.actor, entitlements=tuple(item for item in stock.actor.entitlements if item.action!=action))
    stock.world.current_principal = actor
    with pytest.raises(material.InventoryPostingError) as exc:
        seals.seal_command(db, **{**args(stock), 'actor':actor})
    assert exc.value.category == 'forbidden'
    assert not db.new


@pytest.mark.parametrize('change', [{'request_id':'bad'}, {'request_hash':'X'*64}, {'operation_type':'recover'}])
def test_invalid_seal_input_creates_no_fact(db, stock, change):
    with pytest.raises(material.InventoryPostingError) as exc:
        seals.seal_command(db, **{**args(stock), **change})
    assert exc.value.code == 'work_order_seal_input_invalid'
    assert not db.new
