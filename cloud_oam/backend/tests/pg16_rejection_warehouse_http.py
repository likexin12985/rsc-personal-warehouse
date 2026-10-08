"""Actual warehouse HTTP over native PG16; identities and attachments are synthetic."""
from contextlib import contextmanager
from decimal import Decimal
import json
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.main import app
from app.database import get_db
from app.dependencies import get_formal_principal
from app.config import get_settings
from app.formal_access import load_formal_principal
from app.formal_services.material_request_lifecycle import _canonical_hash
from app.material_request_rejection_receipt_schemas import RejectionReceiptOut
from app.material_request_rejection_inbound_schemas import RejectionInboundOut
from app.material_request_rejection_warehouse_schemas import (
    RejectionWarehouseDetailOut, RejectionWarehouseInboxOut, RejectionWarehousePreviewOut)
from test_material_request_draft_service import SECRET


def path(return_id, receipt_id=None):
    base = f'/api/v1/rejection-returns/{return_id}/warehouse-receipts'
    return base if receipt_id is None else base + f'/{receipt_id}/inbounds'


@contextmanager
def client_for(db, *, actor_id, writes):
    actor = load_formal_principal(db, actor_id)
    def sessions(): yield db
    settings = get_settings().model_copy(update={'material_request_writes_enabled': writes,
        'material_request_idempotency_hmac_secret': SECRET.decode()})
    with patch.object(app, 'dependency_overrides', {**app.dependency_overrides, get_db: sessions,
            get_formal_principal: lambda: actor, get_settings: lambda: settings}), TestClient(app) as client:
        yield client


def post(db, *, actor_id, return_id, value, key, receipt_id=None):
    with client_for(db, actor_id=actor_id, writes=True) as client:
        response = client.post(path(return_id, receipt_id), json=value.model_dump(mode='json'),
            headers={'Idempotency-Key': key, 'X-Request-ID': key})
    assert response.status_code == 201, response.text
    assert 'no-store' in response.headers['cache-control']
    assert response.headers['idempotency-replayed'] == 'false'
    return (RejectionReceiptOut if receipt_id is None else RejectionInboundOut).model_validate(response.json())


def preview(db, *, actor_id, return_id, receipt_id, expected, directory):
    with client_for(db, actor_id=actor_id, writes=False) as client:
        response = client.get(path(return_id, receipt_id) + '/preview')
    assert response.status_code == 200, response.text
    assert 'no-store' in response.headers['cache-control']
    result = RejectionWarehousePreviewOut.model_validate(response.json())
    assert result.plan_hash == expected['plan_hash']
    assert result.receipt_request_hash == expected['plan']['receipt_request_hash']
    assert [(p.condition_code, p.quantity, [str(s.serial_id) for s in p.serials]) for p in result.parts] == [
        (p['condition_code'], p['quantity'], p['serial_ids']) for p in expected['plan']['parts']]
    assert 'account_id' not in response.text and 'actor_user_id' not in response.text
    (directory / 'warehouse-public-preview.json').write_text(json.dumps(response.json(), indent=2) + '\n')
    return result


def verify_recovery(api, *, actor_id, return_id, value, key, expected, directory, receipt_id=None):
    def sessions():
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            yield db
    def principal():
        with Session(api) as db: return load_formal_principal(db, actor_id)
    settings = get_settings().model_copy(update={'material_request_writes_enabled': False,
        'material_request_idempotency_hmac_secret': SECRET.decode()})
    identity = 'receipt_id' if receipt_id is None else 'inbound_id'
    stage = 'acceptance' if receipt_id is None else 'inbound'
    with patch.object(app, 'dependency_overrides', {**app.dependency_overrides, get_db: sessions,
            get_formal_principal: principal, get_settings: lambda: settings}), TestClient(app) as client:
        for header in ('Idempotency-Key', 'X-Original-Request-ID'):
            response = client.get(path(return_id, receipt_id) + '/command-status', headers={header: key,
                'X-Request-Fingerprint': _canonical_hash(value.model_dump(mode='json'))})
            assert response.status_code == 200, response.text
            assert 'no-store' in response.headers['cache-control']
            body = response.json()
            assert body['lookup_status'] == 'confirmed' and body['command'][identity] == str(getattr(expected, identity))
            assert body['command']['replayed']
        wrong = client.get(path(return_id, receipt_id) + '/command-status', headers={
            'Idempotency-Key': key, 'X-Request-Fingerprint': '0' * 64})
        assert wrong.status_code == 409, wrong.text
        assert client.post(path(return_id, receipt_id), json=value.model_dump(mode='json'),
            headers={'Idempotency-Key': key, 'X-Request-ID': key}).status_code == 503
        response = client.get(f'/api/v1/rejection-returns/{return_id}/warehouse')
        assert response.status_code == 200, response.text
        detail = RejectionWarehouseDetailOut.model_validate(response.json())
        assert not detail.receive_permitted and all(not row.post_permitted for row in detail.receipts)
        row = next(row for row in detail.receipts if row.receipt.receipt_id == expected.receipt_id)
        if receipt_id is None:
            assert row.inbound is None and detail.posted_qty == '0.000'
            assert Decimal(detail.pending_inbound_qty) == expected.amounts.accepted_qty
        else:
            assert row.inbound.inbound_id == expected.inbound_id and detail.pending_inbound_qty == '0.000'
            assert Decimal(detail.posted_qty) == Decimal(detail.accepted_qty)
        (directory / f'warehouse-public-{stage}-detail.json').write_text(json.dumps(response.json(), indent=2) + '\n')
        response = client.get('/api/v1/rejection-returns/my-warehouse?limit=20')
        assert response.status_code == 200, response.text
        page = RejectionWarehouseInboxOut.model_validate(response.json())
        assert page.next_after_id is None
        assert next(row.detail for row in page.items if row.return_id == return_id) == detail
        (directory / f'warehouse-public-{stage}-inbox.json').write_text(json.dumps(response.json(), indent=2) + '\n')
    return dict(firstPostStatus=201, keyAndTraceRecovery=True, fingerprintMismatchStatus=409,
        recoveryAfterRevocation=True, readOnly=True, writeSwitchClosed=True, scopedWarehouseHistoryVerified=True)
