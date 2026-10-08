"""Disposable structural checks, not proof of registrar/authority/COMMIT guards."""
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.database import Base
from app.stock_operation_models import StockLossHeadquartersDecision, StockOperationLine
from app.stock_scrap_seal_schema import ALIASES, NAME, build_schema, define
from app.formal_services import stock_loss_sources
from app.formal_services.stock_scrap.lookup_coordinates import keys
from app.formal_services.stock_scrap.request_lookup import canonical
from test_stock_scrap_request_lookup import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, original,
)
from test_stock_scrap_recovery_approval import stock_state


def candidate(db):
    _, table = build_schema()
    table.create(db.connection(),checkfirst=True)
    return table


def original_row(db, original):
    command, actor = original.command, original.actor
    decision = db.get(StockLossHeadquartersDecision, command.source.headquarters_decision_id)
    line = db.get(StockOperationLine, decision.line_id)
    hashes = keys(command)
    document = canonical(command)
    return dict(id=uuid4(), created_at=datetime.now(timezone.utc),
        actor_user_id=actor.user_id, actor_person_id=actor.person_id,
        authorization_version=actor.authorization_version, request_id=command.request_id,
        idempotency_key_hash=hashes[4], request_hash=stock_loss_sources._hash(document),
        reason=command.execution_reason, command_jsonb=document, kind='original',
        loss_operation_id=line.operation_id, loss_line_id=line.id,
        original_decision_id=decision.id, scrap_disposition='scrap',
        plan_hash=command.expected_plan_hash,
        key_token=sha256(('cloud_oam.loss.correction.key.v1\0'+command.idempotency_key).encode()).hexdigest(),
        **dict(zip(ALIASES, hashes)))


def test_real_original_can_be_sealed_without_manufacturing_a_disposition(db, original):
    table = candidate(db)
    before = stock_state(db)
    row = original_row(db, original)
    assert db.scalar(select(func.count()).select_from(Base.metadata.tables['stock_loss_dispositions'])) == 0
    db.execute(table.insert().values(**row))
    db.commit()
    saved = db.execute(select(table)).mappings().one()
    assert saved['original_decision_id'] == row['original_decision_id']
    assert saved['root_disposition_id'] is None
    assert saved['command_jsonb'] == row['command_jsonb']
    assert stock_state(db) == before
    assert db.scalar(select(func.count()).select_from(Base.metadata.tables['stock_loss_dispositions'])) == 0
    assert NAME in Base.metadata.tables


def test_schema_rejects_incomplete_sources_and_invalid_request_provenance(db, original):
    table = candidate(db)
    row = original_row(db, original)
    before = stock_state(db)
    # Exercise actual INSERT constraints. Each rejected row gets its own
    # savepoint so the real approved loss and its immutable history survive.
    changes = [
        dict(original_decision_id=None), dict(original_decision_id=uuid4()),
        dict(loss_operation_id=uuid4()), dict(loss_line_id=uuid4()),
        dict(root_disposition_id=uuid4()), dict(plan_hash=None),
        dict(plan_hash='A'*64), dict(kind='unknown'),
        dict(kind='correction'), dict(kind='apply'), dict(kind='regional'),
        dict(kind='headquarters'), dict(kind='execute'),
        dict(scrap_disposition='return'), dict(regional_decision='verified'),
        dict(idempotency_key_hash=row['recovery_key_hash']),
        dict(recovery_key_hash=row['scrap_key_hash']),
        dict(key_token='z'*64), dict(request_hash='x'*64),
        dict(request_id='invalid space'), dict(authorization_version=0),
    ]
    for change in changes:
        with pytest.raises(IntegrityError), db.begin_nested():
            db.execute(table.insert().values(**(row | change)))
        assert db.scalar(select(func.count()).select_from(table)) == 0, change
    assert stock_state(db) == before


def test_global_key_and_actor_request_cannot_be_sealed_twice(db, original):
    table = candidate(db)
    row = original_row(db, original)
    db.execute(table.insert().values(**row))
    db.commit()
    # Isolate token uniqueness from the other aliases and actor/request index.
    independent = row | dict(id=uuid4(), request_id='independent-seal-request',
        **{name: sha256(('independent:'+name).encode()).hexdigest() for name in ALIASES})
    independent['idempotency_key_hash'] = independent['scrap_key_hash']
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(table.insert().values(**independent))
    independent.update(key_token=sha256(b'independent-token').hexdigest(), request_id=row['request_id'])
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(table.insert().values(**independent))
    assert db.scalar(select(func.count()).select_from(table)) == 1


def test_candidate_cannot_install_itself_into_live_metadata():
    with pytest.raises(ValueError, match='live Base'):
        define(Base.metadata)
