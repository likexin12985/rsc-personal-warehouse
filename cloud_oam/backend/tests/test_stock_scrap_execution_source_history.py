"""Real service generations and query-only source reads; bindings are SQLite fixtures.

The native HTTP gate independently proves registrar, role ACL and COMMIT behavior.
"""
import hashlib
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from app.stock_operation_models import StockLossDisposition
from app.foundation_models import Permission, Role, RolePermission
from app.formal_access import load_formal_principal
from app.stock_loss_correction_models import stock_loss_request_key_bindings as bindings
from app.formal_services import stock_loss_execution_sources as sources
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import original_recovery
from app.formal_services.stock_scrap.lookup_coordinates import ALIASES, keys
from app.formal_services.stock_scrap.binding_reads import REGISTRY, TYPED
from scrap_lookup_binding_fixture import create
from test_stock_scrap_recovery_generations import exercise_generations
from test_stock_scrap_recovery_execution import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, found, ready_to_restore,
    service, snapshot,
)


def bind(db, kind, fact, command):
    create(db)
    hashes = keys(command)
    special = ('recovery_key_hash' if kind == 'inverse' else 'scrap_key_hash' if kind == 'correction' else None)
    value = dict(fact_id=fact.id, binding_kind=kind, root_disposition_id=fact.root_disposition_id,
        actor_user_id=fact.actor_user_id, request_id=fact.request_id, request_hash=fact.request_hash,
        created_at=fact.created_at,
        key_token=hashlib.sha256(('cloud_oam.loss.correction.key.v1\0'+command.idempotency_key).encode()).hexdigest(),
        **{column: hashes[i] if i < 3 or column == special else None for i,column in enumerate(ALIASES)},
        **{column: fact.id if column == kind+'_id' else None for column in
            ('inverse_id','approval_id','correction_id','seal_id','approval_seal_id','correction_seal_id')})
    db.execute(bindings.insert().values(**value)); db.commit()
    return value


def state(db):
    return snapshot(db), tuple(db.execute(select(bindings).order_by(bindings.c.fact_id))), tuple(db.execute(select(REGISTRY).order_by(REGISTRY.c.fact_id)))


def test_source_read_survives_both_scrap_recovery_generations(db, ready_to_restore, monkeypatch):
    w = ready_to_restore
    role = db.scalar(select(Role).where(Role.code == 'admin'))
    permission = Permission(resource='stock_operation', action='read', field_code='', description='Synthetic source read')
    db.add(permission); db.flush()
    db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow')); db.commit()
    w.actor = load_formal_principal(db, w.actor.user_id)
    w.found.world.current_principal = w.actor
    stages = []
    def observe(kind, fact, command):
        bind(db, kind, fact, command)
        root = db.get(StockLossDisposition, fact.root_disposition_id)
        before = state(db)
        db.execute(text('PRAGMA query_only=ON'))
        try:
            result = sources.execution_sources(db, actor=w.found.world.current_principal, operation_id=root.operation_id)
            assert len(result.decisions) == 1
            posted = result.decisions[0].original_posting
            assert posted.disposition_id == root.id and posted.posting_transaction_id == root.posting_transaction_id
            assert posted.quantity == root.quantity
            assert state(db) == before and not db.new and not db.dirty and not db.deleted
            stages.append(kind)
        finally:
            db.execute(text('PRAGMA query_only=OFF'))
    exercise_generations(db, w, monkeypatch, observe=observe)
    assert stages == ['inverse','approval','correction','inverse']


def test_recovery_alias_corruption_and_foreign_registry_collision_fail_closed(db, ready_to_restore):
    from app.stock_loss_correction_models import StockLossDispositionReversal
    w = ready_to_restore
    posted = service.execute(db, actor=w.actor, request=w.command); db.commit()
    fact = db.get(StockLossDispositionReversal, UUID(posted['reversal_id']))
    original = bind(db, 'inverse', fact, w.command)
    root = db.get(StockLossDisposition, fact.root_disposition_id)
    assert original_recovery.verified(db, row=root)['disposition_id'] == str(root.id)
    for fault in ('missing_alias','wrong_alias','missing_binding','foreign_alias','foreign_token','foreign_request'):
        point = db.begin_nested()
        try:
            if fault == 'missing_binding':
                db.execute(bindings.delete().where(bindings.c.fact_id == fact.id))
            elif fault in ('missing_alias','wrong_alias'):
                db.execute(bindings.update().where(bindings.c.fact_id == fact.id).values(
                    recovery_key_hash=None if fault == 'missing_alias' else 'f'*64))
            else:
                identifier = uuid4()
                foreign = dict(fact_id=identifier, binding_kind='apply', root_disposition_id=root.id,
                    actor_user_id=fact.actor_user_id, actor_person_id=w.actor.person_id,
                    request_id=fact.request_id if fault == 'foreign_request' else uuid4().hex,
                    request_hash='a'*64, key_token=original['key_token'] if fault == 'foreign_token' else 'b'*64,
                    created_at=fact.created_at,
                    **{name: hashlib.sha256(name.encode()).hexdigest() for name in ALIASES},
                    **{name: identifier if kind == 'apply' else None for kind,name in TYPED.items()})
                if fault == 'foreign_alias':
                    foreign['scrap_key_hash'] = original['recovery_key_hash']
                db.execute(REGISTRY.insert().values(**foreign))
            db.flush(); before = state(db)
            db.execute(text('PRAGMA query_only=ON'))
            with pytest.raises(InventoryReadError) as caught:
                original_recovery.verified(db, row=root)
            assert caught.value.status_code == 503 and state(db) == before
        finally:
            db.execute(text('PRAGMA query_only=OFF')); point.rollback(); db.expire_all()
