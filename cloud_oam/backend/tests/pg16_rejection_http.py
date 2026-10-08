"""Actual public routes against the caller-owned native PG16, synthetic identities."""
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
from app.material_request_rejection_return_schemas import RejectionReturnOut
from app.material_request_rejection_progress_schemas import RejectionProgressOut
from test_material_request_draft_service import SECRET


def post(db, *, actor_id, request_id, value, key, return_id=None):
    actor = load_formal_principal(db, actor_id)
    def sessions(): yield db
    settings = get_settings().model_copy(update={'material_request_writes_enabled': True,
        'material_request_idempotency_hmac_secret': SECRET.decode()})
    path = f'/api/v1/material-requests/{request_id}/rejection-returns'
    if return_id is not None: path += f'/{return_id}/progress'
    with patch.object(app, 'dependency_overrides', {**app.dependency_overrides, get_db: sessions,
            get_formal_principal: lambda: actor, get_settings: lambda: settings}), TestClient(app) as client:
        response = client.post(path, json=value.model_dump(mode='json'),
            headers={'Idempotency-Key': key, 'X-Request-ID': key})
    assert response.status_code == 201, response.text
    assert 'no-store' in response.headers['cache-control']
    return (RejectionReturnOut if return_id is None else RejectionProgressOut).model_validate(response.json())


def verify_recovery(api, *, actor_id, request_id, value, key, expected, return_id=None):
    def sessions():
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            yield db
    def principal():
        with Session(api) as db: return load_formal_principal(db, actor_id)
    settings = get_settings().model_copy(update={'material_request_writes_enabled': False,
        'material_request_idempotency_hmac_secret': SECRET.decode()})
    base = f'/api/v1/material-requests/{request_id}/rejection-returns'
    path = base + (f'/{return_id}/progress' if return_id is not None else '')
    identity = 'return_id' if return_id is None else 'event_id'
    with patch.object(app, 'dependency_overrides', {**app.dependency_overrides, get_db: sessions,
            get_formal_principal: principal, get_settings: lambda: settings}), TestClient(app) as client:
        for header in ('Idempotency-Key', 'X-Original-Request-ID'):
            response = client.get(path + '/command-status', headers={header: key,
                'X-Request-Fingerprint': _canonical_hash(value.model_dump(mode='json'))})
            assert response.status_code == 200, response.text
            assert 'no-store' in response.headers['cache-control']
            body = response.json()
            assert body['lookup_status'] == 'confirmed' and body['command'][identity] == str(getattr(expected, identity))
            assert body['command']['replayed']
        wrong = client.get(path + '/command-status', headers={'Idempotency-Key': key, 'X-Request-Fingerprint': '0' * 64})
        assert wrong.status_code == 409, wrong.text
        assert client.post(path, json=value.model_dump(mode='json'),
            headers={'Idempotency-Key': key, 'X-Request-ID': key}).status_code == 503
        response = client.get(base + '/candidates?limit=20')
        assert response.status_code == 200, response.text
        page = response.json()
        assert page['next_after_id'] is None and 'no-store' in response.headers['cache-control']
        source = next(line for receipt in page['items'] for line in receipt['lines']
            if any(h['registration']['return_id'] == str(expected.return_id) for h in line['registrations']))
        history = next(h for h in source['registrations'] if h['registration']['return_id'] == str(expected.return_id))
        assert source['available_qty'] == '0.000' and not source['register_permitted']
        if return_id is not None:
            assert history['progress']['status'] == 'handed_over' and history['permitted_actions'] == []
            state = client.get(path)
            assert state.status_code == 200 and state.json() == history['progress'], state.text
        else:
            assert history['registration']['receipt_request_hash'] == value.receipt_request_hash
    return dict(firstPostStatus=201, keyAndTraceRecovery=True, fingerprintMismatchStatus=409,
        recoveryAfterRevocation=True, readOnly=True, writeSwitchClosed=True, sourceHistoryVerified=True)
