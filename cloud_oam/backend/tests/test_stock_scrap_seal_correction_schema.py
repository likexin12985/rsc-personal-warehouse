"""Seal structure binds an actual inverse and independent correction approval."""
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.formal_services import stock_loss_sources
from app.formal_services.stock_scrap.lookup_coordinates import keys
from app.formal_services.stock_scrap.request_lookup import canonical
from app.stock_scrap_seal_schema import ALIASES
from test_stock_scrap_seal_schema import candidate
from test_stock_scrap_correction_plan import (
    world, stock, allowed, evidence, regional, headquarters, approved, route,
    execution, prepared, recoverable, inverse_ready, ready, command,
)
from stock_scrap_writer_fixture import db
from test_stock_scrap_execution import execute_command
from test_stock_scrap_recovery_approval import stock_state

pytestmark = [pytest.mark.parametrize('execution', ['restore_available'], indirect=True),
    pytest.mark.parametrize('ready', ['scrap'], indirect=True)]


def test_corrected_seal_preserves_real_root_inverse_and_scrap_approval(db, ready, monkeypatch):
    table = candidate(db)
    request, _ = execute_command(db, ready.w.actor, command(db, ready, monkeypatch))
    actor, source = ready.w.actor, request.source
    document, hashes = canonical(request), keys(request)
    before = stock_state(db)
    row = dict(id=uuid4(), created_at=datetime.now(timezone.utc), kind='correction',
        actor_user_id=actor.user_id, actor_person_id=actor.person_id,
        authorization_version=actor.authorization_version, request_id=request.request_id,
        idempotency_key_hash=hashes[4], request_hash=stock_loss_sources._hash(document),
        command_jsonb=document, reason=request.execution_reason,
        loss_operation_id=ready.w.root.operation_id, loss_line_id=ready.w.root.line_id,
        root_disposition_id=source.root_disposition_id, reversal_id=source.reversal_id,
        correction_decision_id=source.correction_decision_id, scrap_disposition='scrap',
        plan_hash=request.expected_plan_hash,
        key_token=sha256(('cloud_oam.loss.correction.key.v1\0'+request.idempotency_key).encode()).hexdigest(),
        **dict(zip(ALIASES, hashes)))
    db.execute(table.insert().values(**row))
    db.commit()
    saved = dict(db.execute(select(table)).mappings().one())
    for change in [dict(root_disposition_id=None), dict(reversal_id=None),
            dict(reversal_id=uuid4()), dict(correction_decision_id=None),
            dict(correction_decision_id=uuid4()), dict(original_decision_id=ready.w.root.headquarters_decision_id),
            dict(scrap_disposition='restore_available'), dict(plan_hash=None)]:
        with pytest.raises(IntegrityError), db.begin_nested():
            db.execute(table.update().where(table.c.id == row['id']).values(**change))
    assert dict(db.execute(select(table)).mappings().one()) == saved
    assert saved['original_decision_id'] is None
    assert stock_state(db) == before
