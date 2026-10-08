"""Cross-action and orphan rejection; SQLite corruption is read-side evidence."""
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from app.foundation_models import OutboxEvent, StateTransitionEvent, NotificationEvent, Permission, RolePermission
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import return_condition_coordinates as coordinates
from app.formal_services.stock_loss_corrections import return_condition_submission as writer
from app.formal_services.stock_loss_corrections import return_condition_recovery as recovery
from test_return_condition_ledger_facts import db, quantity_command, serial_command, authority_template
from test_return_condition_submission import snapshot
from test_return_condition_submission_source import (
    world, stock, allowed, evidence, regional, headquarters, approved, route, derived, ready, parcel,
    acceptance, prepared, regional_opening, reader_tables, context, regional_source, ERRORS,
)


def refused(call):
    with pytest.raises(InventoryReadError) as error:
        call()
    assert error.value.code == 'return_condition_request_outcome_unknown'


def orphan(db, actor, request, model, *, reference=False, other_actor=False):
    from app.formal_services.inventory_posting import _request_reference
    body = ({'request_reference': _request_reference(request.request_id)} if reference else
            {'request_id': request.request_id})
    # A claimed foreign actor without its immutable audit is still unknown.
    body['actor_user_id'] = str(uuid4()) if other_actor else actor.user_id
    identifier = str(uuid4())
    if model is OutboxEvent:
        row = model(event_type='stock_condition.submit', aggregate_type='detached_condition',
            aggregate_id=identifier, payload_jsonb=body, idempotency_key=uuid4().hex,
            available_at=datetime.now(timezone.utc))
    elif model is StateTransitionEvent:
        row = model(aggregate_type='detached_condition', aggregate_id=identifier,
            from_status='draft', to_status='submitted', actor_id=actor.user_id,
            reason='uncertain request', idempotency_key=uuid4().hex,
            occurred_at=datetime.now(timezone.utc), metadata_jsonb=body)
    else:
        row = model(event_type='stock_condition.submit', business_type='detached_condition',
            business_id=identifier, dedup_key=uuid4().hex, payload_jsonb=body,
            occurred_at=datetime.now(timezone.utc), target_manifest_sha256='a'*64)
    db.add(row); db.flush()


@pytest.mark.parametrize('stock,command_name', [('quantity','quantity_command'),('serial','serial_command')], indirect=['stock'])
def test_legacy_aliases_and_registry_block_submission_before_any_stock_write(db, regional_source, request, command_name):
    c = regional_source; command = request.getfixturevalue(command_name)
    before = snapshot(db)
    orders = writer.tables()['stock_operation_orders']
    row = db.execute(select(orders).where(orders.c.actor_user_id != c.actor.user_id).limit(1)).mappings().one()
    # The same raw key in all eleven old/current key namespaces conflicts even
    # under another actor and request number. Changes roll back each iteration.
    for key in coordinates.hashes(command):
        db.execute(update(orders).where(orders.c.id == row['id']).values(idempotency_key_hash=key))
        corrupted = snapshot(db)
        refused(lambda: writer.submit(db, actor=c.actor, request=command))
        assert snapshot(db) == corrupted
        db.rollback(); assert snapshot(db) == before
    registry = writer.tables()['stock_scrap_request_key_bindings']
    root = db.execute(select(writer.tables()['stock_loss_dispositions'])).mappings().first()
    aliases = ('reversal_key_hash','approval_key_hash','correction_key_hash','recovery_key_hash','scrap_key_hash')
    base = dict(fact_id=root['id'], binding_kind='original', root_disposition_id=root['id'],
        actor_user_id=c.reviewer.user_id, actor_person_id=c.reviewer.person_id,
        request_id=uuid4().hex, request_hash='b'*64, key_token='c'*64,
        original_id=root['id'], application_id=None, regional_id=None, headquarters_id=None,
        created_at=root['created_at'], **{name:sha256(name.encode()).hexdigest() for name in aliases})
    changes = [dict(key_token=sha256(('cloud_oam.loss.correction.key.v1\0'+command.idempotency_key).encode()).hexdigest()),
        dict(actor_user_id=c.actor.user_id,actor_person_id=c.actor.person_id,request_id=command.request_id),
        *(dict({name:coordinates.hashes(command)[0]}) for name in aliases)]
    for change in changes:
        db.execute(registry.insert(), base | change)
        corrupted = snapshot(db)
        refused(lambda: coordinates.require_unused(db,actor=c.actor,request=command))
        refused(lambda: writer.submit(db,actor=c.actor,request=command))
        refused(lambda: recovery.lookup(db,actor=c.actor,request=command))
        assert snapshot(db) == corrupted
        db.rollback(); assert snapshot(db) == before


@pytest.mark.parametrize('stock,command_name', [('quantity','quantity_command'),('serial','serial_command')], indirect=['stock'])
def test_detached_effects_block_missing_and_found_outcomes_and_late_read(db, regional_source, request, command_name, monkeypatch):
    c = regional_source; command = request.getfixturevalue(command_name)
    for found in (False, True):
        if found:
            result = writer.submit(db,actor=c.actor,request=command); db.commit()
            assert recovery.lookup(db,actor=c.actor,request=command)['result'] == result
            db.rollback()
        before = snapshot(db)
        for model in (OutboxEvent, StateTransitionEvent, NotificationEvent):
            for reference in (False, True):
                orphan(db,c.actor,command,model,reference=reference)
                corrupted = snapshot(db)
                refused(lambda: recovery.lookup(db,actor=c.actor,request=command))
                if not found:
                    refused(lambda: writer.submit(db,actor=c.actor,request=command))
                assert snapshot(db) == corrupted
                db.rollback(); assert snapshot(db) == before
        orphan(db,c.actor,command,OutboxEvent,other_actor=True)
        refused(lambda: recovery.lookup(db,actor=c.actor,request=command))
        db.rollback(); assert snapshot(db) == before
        # Introduce an unattributed outbox after the first scan. It does not
        # advance the inventory/audit heads, so the second coordinate scan is
        # necessary. This mutation is a test race, never reader behavior.
        real_read = recovery.history.read
        def race(*args, **kwargs):
            proof = real_read(*args, **kwargs)
            orphan(db,c.actor,command,OutboxEvent)
            return proof
        with monkeypatch.context() as patch:
            patch.setattr(recovery.history,'read',race)
            refused(lambda: recovery.lookup(db,actor=c.actor,request=command))
        db.rollback(); assert snapshot(db) == before
        # Also revoke read access at the end of the second coordinate scan,
        # after all graph checks, without changing any stock/audit boundary.
        grant = db.scalar(select(RolePermission.id).join(Permission).where(
            RolePermission.role_id==c.regional_role.id,
            Permission.resource=='stock_operation',Permission.action=='read'))
        real_capture = coordinates.capture
        count = 0
        def revoke(*args, **kwargs):
            nonlocal count
            value = real_capture(*args, **kwargs); count += 1
            if count == 2:
                db.execute(update(RolePermission).where(RolePermission.id==grant).values(effect='deny'))
            return value
        with monkeypatch.context() as patch:
            patch.setattr(coordinates,'capture',revoke)
            with pytest.raises(InventoryReadError) as error:
                recovery.lookup(db,actor=c.actor,request=command)
            assert error.value.code=='return_condition_history_forbidden'
            assert count==2
        db.rollback(); assert snapshot(db)==before
