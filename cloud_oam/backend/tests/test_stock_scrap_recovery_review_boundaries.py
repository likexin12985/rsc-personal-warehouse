"""Recovery denial, retries after evidence rejection, and transaction failures."""
from dataclasses import replace
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.inventory_models import InventorySerial
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_scrap import recovery_approval as service, recovery_events as events
from app.formal_services.stock_scrap.tables import tables
from test_stock_scrap_recovery_approval import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, found,
    submit, region_request, hq_request, stock_state, all_state, coordinates, upload,
)


def test_needs_evidence_requires_new_application_and_fresh_attachment(db, found, monkeypatch):
    applied = submit(db, found, 'apply', found.application)
    rejected = submit(db, found, 'regional', region_request(found, applied, 'needs_evidence'))
    assert rejected['status'] == 'needs_evidence'
    with pytest.raises(InventoryReadError):
        submit(db, found, 'regional', region_request(found, applied))
    db.rollback()
    found.world.current_principal = found.actors['apply']
    new_file = upload(db, found.actors['apply'], monkeypatch)
    value = found.application.model_copy(update=dict(evidence_file_ids=(new_file.id,), **coordinates()))
    newer = submit(db, found, 'apply', value)
    assert newer['fact_id'] != applied['fact_id']
    table = tables()['stock_scrap_recovery_requests']
    assert len(db.execute(select(table)).all()) == 2
    region = submit(db, found, 'regional', region_request(found, newer))
    assert submit(db, found, 'headquarters', hq_request(found, newer, region))['status'] == 'approved_pending_execution'


def test_recovery_idempotency_key_cannot_cross_stages(db, found):
    applied = submit(db, found, 'apply', found.application)
    value = region_request(found, applied).model_copy(update={'idempotency_key': found.application.idempotency_key})
    before = all_state(db), stock_state(db)
    found.world.current_principal = found.actors['regional']
    with pytest.raises(InventoryReadError) as error:
        service.submit(db, actor=found.actors['regional'], request=value)
    assert error.value.code == 'stock_scrap_recovery_request_requires_lookup'
    db.rollback()
    assert (all_state(db), stock_state(db)) == before


@pytest.mark.parametrize('same_as', ['apply', 'regional'])
def test_hq_cannot_be_applicant_or_regional_reviewer(db, found, same_as):
    applied = submit(db, found, 'apply', found.application)
    region = submit(db, found, 'regional', region_request(found, applied))
    other = found.actors[same_as]
    actor = replace(found.actors['headquarters'], user_id=other.user_id, person_id=other.person_id,
        authorization_version=other.authorization_version)
    found.world.current_principal = actor
    before = all_state(db), stock_state(db)
    with pytest.raises(InventoryReadError) as error:
        service.submit(db, actor=actor, request=hq_request(found, applied, region))
    assert error.value.code == 'stock_scrap_recovery_self_review'
    db.rollback()
    assert (all_state(db), stock_state(db)) == before


def test_notification_failure_rolls_back_application_and_bound_evidence(db, found, monkeypatch):
    before = all_state(db), stock_state(db)
    def fail(*args, **kwargs):
        raise RuntimeError('synthetic recovery notification failure')
    monkeypatch.setattr(events, 'record_business_notification', fail)
    with pytest.raises(RuntimeError, match='synthetic recovery notification failure'):
        service.submit(db, actor=found.actors['apply'], request=found.application)
    db.rollback()
    assert (all_state(db), stock_state(db)) == before


def test_mutable_serial_cache_cannot_claim_found_stock(db, found, allowed):
    if not allowed.tracked:
        pytest.skip('quantity mode has no serial lifecycle cache')
    serial = db.get(InventorySerial, allowed.serials[3].id)
    assert serial.lifecycle_status == 'scrapped'
    serial.lifecycle_status = 'active'
    db.commit()
    before = all_state(db), stock_state(db)
    with pytest.raises(InventoryReadError) as error:
        service.submit(db, actor=found.actors['apply'], request=found.application)
    assert error.value.code == 'stock_scrap_recovery_serial_changed'
    db.rollback()
    assert (all_state(db), stock_state(db)) == before
