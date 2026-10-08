"""Closure grant checks must never weaken actual physical recovery authority."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from app.inventory_models import CustodyAssignment
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from app.formal_services.stock_scrap import recovery_authority as authority, recovery_execution
from app.formal_services.stock_scrap.tables import tables
from test_stock_scrap_recovery_execution import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    found, ready_to_restore, all_state, stock_state,
)


def ancestors(db, w):
    source = authority.load_source(db, w.command.source)
    application = tables()['stock_scrap_recovery_requests']
    applicant = dict(db.execute(select(application).where(application.c.id == w.command.recovery_request_id)).mappings().one())
    regional = tables()['stock_scrap_recovery_regional_reviews']
    region = dict(db.execute(select(regional).where(regional.c.recovery_request_id == applicant['id'])).mappings().one())
    return source, applicant, region


def test_expired_custody_does_not_prevent_closure_grant_but_still_blocks_stock_recovery(db, ready_to_restore):
    w = ready_to_restore
    source, applicant, region = ancestors(db, w)
    custody = db.scalars(select(CustodyAssignment).where(
        CustodyAssignment.location_id == source['account'].location_id)).one()
    custody.valid_to = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    before = all_state(db), stock_state(db)
    for stage in authority.ACTIONS:
        actor = w.actor if stage == 'execute' else w.found.actors[stage]
        w.found.world.current_principal = actor
        args = dict(actor=actor, source=source, stage=stage,
            applicant=applicant if stage != 'apply' else None,
            regional=region if stage == 'headquarters' else None)
        assert authority.authorize_closure(db, **args) == actor
        with pytest.raises(InventoryReadError) as error:
            authority.authorize(db, **args)
        assert error.value.code == 'loss_reversal_custody_changed'
    w.found.world.current_principal = w.actor
    with pytest.raises(InventoryReadError) as error:
        recovery_execution.execute(db, actor=w.actor, request=w.command)
    assert error.value.code == 'loss_reversal_custody_changed'
    db.rollback()
    assert (all_state(db), stock_state(db)) == before


def test_closure_preserves_current_grant_scope_identity_and_independent_reviews(db, ready_to_restore):
    w = ready_to_restore
    source, applicant, region = ancestors(db, w)
    before = all_state(db), stock_state(db)
    actor = w.found.actors['regional']
    for attack in ('missing_grant', 'wrong_scope', 'borrowed_role', 'self', 'stale'):
        supplied = actor
        if attack == 'missing_grant':
            supplied = replace(actor, entitlements=tuple(g for g in actor.entitlements if g.action != authority.ACTIONS['regional']))
        elif attack == 'wrong_scope':
            supplied = replace(actor, assignments=tuple(replace(g, scope_id=str(uuid4())) for g in actor.assignments))
        elif attack == 'borrowed_role':
            supplied = replace(actor, entitlements=tuple(replace(g, role_code='admin') for g in actor.entitlements))
        elif attack == 'self':
            supplied = replace(actor, user_id=applicant['actor_user_id'], person_id=applicant['actor_person_id'],
                authorization_version=w.found.actors['apply'].authorization_version)
        w.found.world.current_principal = replace(supplied, authorization_version=supplied.authorization_version+1) if attack == 'stale' else supplied
        with pytest.raises((InventoryReadError, InventoryPostingError)):
            authority.authorize_closure(db, actor=supplied, source=source, stage='regional', applicant=applicant)
    actor = w.found.actors['headquarters']
    w.found.world.current_principal = actor
    for prior in (None, region | {'decision': 'needs_evidence'}, region | {'recovery_request_id': uuid4()}):
        with pytest.raises(InventoryReadError):
            authority.authorize_closure(db, actor=actor, source=source, stage='headquarters',
                applicant=applicant, regional=prior)
    with pytest.raises(InventoryReadError):
        authority.authorize_closure(db, actor=actor, source=source, stage='headquarters',
            applicant=applicant, regional=region | {'actor_person_id': actor.person_id})
    assert (all_state(db), stock_state(db)) == before
