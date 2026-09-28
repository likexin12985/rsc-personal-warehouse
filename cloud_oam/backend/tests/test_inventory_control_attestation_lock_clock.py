"""Clock boundaries around a locked snapshot must govern new and replayed claims."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, func

from app import inventory_control_attestation as service
from app.inventory_control_attestation_models import InventoryControlCaptureAttestation as Receipt
from test_inventory_control_attestation import db, preparation_db, world, signed


def test_replay_rereads_clock_after_another_request_created_the_locked_receipt(db, world, monkeypatch):
    world['stage'](claim=False)
    payload = service.CaptureAttestationIn.model_validate(world['outbox']['controlAttestation'])
    proof = signed(world['outbox']['controlAttestation'])
    first = service.accept_inventory_control_attestation(db, payload=payload, verified=proof)
    db.commit()
    row = db.scalar(select(Receipt))
    created = service._aware(row.created_at)
    assert created > proof.authenticated_at
    before_wait = proof.authenticated_at + (created-proof.authenticated_at)/2
    clock = iter((before_wait, created+timedelta(milliseconds=1)))
    monkeypatch.setattr(service, '_clock', lambda db: next(clock))
    before = (row.id, row.payload_sha256, row.created_at)
    replay = service.accept_inventory_control_attestation(db, payload=payload, verified=proof)
    db.commit()
    assert replay == dict(first, duplicate=True)
    assert db.scalar(select(func.count()).select_from(Receipt)) == 1
    db.refresh(row)
    assert (row.id, row.payload_sha256, row.created_at) == before


@pytest.mark.parametrize('existing_receipt', [False, True])
def test_request_expiring_while_waiting_is_rejected_even_for_exact_replay(db, world, monkeypatch, existing_receipt):
    world['stage'](claim=existing_receipt)
    payload = service.CaptureAttestationIn.model_validate(world['outbox']['controlAttestation'])
    proof = signed(world['outbox']['controlAttestation'])
    now = datetime.now(timezone.utc)
    clock = iter((now, now+timedelta(minutes=6)))
    monkeypatch.setattr(service, '_clock', lambda db: next(clock))
    before = tuple(db.scalars(select(Receipt.id)))
    with pytest.raises(service.CaptureAttestationError, match='expired_while_waiting'):
        service.accept_inventory_control_attestation(db, payload=payload, verified=proof)
    db.rollback()
    assert tuple(db.scalars(select(Receipt.id))) == before
