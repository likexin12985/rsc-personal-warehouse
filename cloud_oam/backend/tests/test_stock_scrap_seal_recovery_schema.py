"""Real recovery parent chains exercise candidate composite seal FKs.

Rows are inserted by the disposable fixture owner, not the future registrar.
This proves structural restrictions only, not permission to close a request.
"""
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.formal_services.stock_scrap import recovery_facts
from app.formal_services.stock_scrap.lookup_coordinates import keys
from app.formal_services.stock_scrap.tables import tables
from app.formal_services import stock_loss_sources
from app.stock_operation_models import StockLossDisposition
from app.stock_scrap_seal_schema import ALIASES
from test_stock_scrap_seal_schema import candidate
from test_stock_scrap_recovery_execution import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    found, ready_to_restore, stock_state, all_state,
)
from test_stock_scrap_recovery_approval import region_request, hq_request, coordinates


def recovery_row(command, actor, root, kind, **parents):
    hashes = keys(command)
    canonical = recovery_facts.intent(command)
    return dict(id=uuid4(), created_at=datetime.now(timezone.utc), kind=kind,
        actor_user_id=actor.user_id, actor_person_id=actor.person_id,
        authorization_version=actor.authorization_version,
        request_id=command.request_id, idempotency_key_hash=hashes[3],
        request_hash=stock_loss_sources._hash(canonical), command_jsonb=canonical,
        reason=command.reason, loss_operation_id=root.operation_id, loss_line_id=root.line_id,
        root_disposition_id=root.id, scrap_line_id=command.source.scrap_line_id,
        scrap_disposition='scrap',
        key_token=sha256(('cloud_oam.loss.correction.key.v1\0'+command.idempotency_key).encode()).hexdigest(),
        **dict(zip(ALIASES, hashes)), **parents)


def test_four_recovery_seal_shapes_bind_real_approved_parents_without_stock_changes(db, ready_to_restore):
    w = ready_to_restore
    table = candidate(db)
    root = db.get(StockLossDisposition, UUID(w.found.scrap['root_disposition_id']))
    final_table = tables()[recovery_facts.NAMES['headquarters']]
    final = db.execute(select(final_table).where(final_table.c.id == w.command.headquarters_review_id)).mappings().one()
    region_table = tables()[recovery_facts.NAMES['regional']]
    region = db.execute(select(region_table).where(region_table.c.id == final['regional_review_id'])).mappings().one()
    applied = dict(fact_id=w.command.recovery_request_id, request_hash=w.command.expected_request_hash)
    application = w.found.application.model_copy(update=coordinates())
    regional_command = region_request(w.found, applied)
    hq_command = hq_request(w.found, applied, dict(fact_id=region['id'], request_hash=region['request_hash']))
    rows = [
        recovery_row(application, w.found.actors['apply'], root, 'apply'),
        recovery_row(regional_command, w.found.actors['regional'], root, 'regional',
            recovery_request_id=w.command.recovery_request_id),
        recovery_row(hq_command, w.found.actors['headquarters'], root, 'headquarters',
            recovery_request_id=w.command.recovery_request_id,
            regional_review_id=region['id'], regional_decision='verified'),
        recovery_row(w.command, w.actor, root, 'execute', recovery_request_id=w.command.recovery_request_id,
            headquarters_review_id=final['id'], headquarters_decision='approve', plan_hash=w.command.expected_plan_hash),
    ]
    before = stock_state(db), all_state(db)
    for row in rows:
        # Separate inserts: SQLAlchemy executemany must not infer optional
        # source columns from the first, minimal application shape.
        db.execute(table.insert().values(**row))
    db.commit()
    assert (stock_state(db), all_state(db)) == before
    saved = {r['kind']: r for r in db.execute(select(table)).mappings()}
    assert set(saved) == {'apply', 'regional', 'headquarters', 'execute'}
    assert all(saved[k]['plan_hash'] is None for k in ('apply', 'regional', 'headquarters'))
    assert saved['execute']['plan_hash'] == w.command.expected_plan_hash
    assert saved['execute']['regional_review_id'] is None

    # Mutations deliberately bypass the service in this owner-only fixture.
    # Check CHECK-UNKNOWN holes as well as invalid composite FK bindings.
    attacks = [
        ('apply', dict(root_disposition_id=None)),
        ('apply', dict(plan_hash='a'*64)),
        ('regional', dict(recovery_request_id=None)),
        ('regional', dict(recovery_request_id=uuid4())),
        ('headquarters', dict(regional_review_id=None)),
        ('headquarters', dict(regional_review_id=uuid4())),
        ('headquarters', dict(regional_decision=None)),
        ('headquarters', dict(regional_decision='needs_evidence')),
        ('execute', dict(headquarters_review_id=None)),
        ('execute', dict(headquarters_review_id=uuid4())),
        ('execute', dict(headquarters_decision=None)),
        ('execute', dict(headquarters_decision='request_regional_review')),
        ('execute', dict(plan_hash=None)),
    ]
    for kind, values in attacks:
        with pytest.raises(IntegrityError), db.begin_nested():
            db.execute(table.update().where(table.c.id == saved[kind]['id']).values(**values))
    assert {r['kind']: dict(r) for r in db.execute(select(table)).mappings()} == {
        k: dict(v) for k, v in saved.items()}
