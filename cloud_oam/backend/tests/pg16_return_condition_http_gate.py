"""Full application ASGI on owned PG16, real API role and default grants.

Only login identity and object storage are test boundaries. Every request loads
the current principal from real SQL. No business service, response, permission,
constraint or COMMIT is substituted. This is not network deployment or UAT.
"""
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import FileObject, Organization, Permission, Role, RoleAssignment, RolePermission
from app.inventory_models import FormalMaterial
from app.models import User
from app.routers.formal_files import get_formal_file_storage_adapter
from app.stock_operation_models import StockOperationReturnInbound
from app.formal_services.stock_loss_corrections.return_condition_authority import ACTIONS
from pg16_return_condition_submission_gate import snapshot
from pg16_return_condition_decision_seals_business import stock
from test_formal_access import make_user, assign
from test_formal_files_service import FakeStorage, SECRET

BASE = '/api/v1/stock-operations/loss-reports/return-condition-corrections'
FILES = '/api/v1/files'


@contextmanager
def http(api, user_id, storage):
    from app.main import app
    settings = Settings(_env_file=None, file_storage_enabled=True, file_storage_provider='aliyun_oss_v2',
        file_storage_region='cn-shanghai', file_storage_bucket='synthetic-private',
        file_idempotency_hmac_secret=SECRET)
    def database():
        with Session(api) as db:
            assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
            yield db
    def principal(db=Depends(get_db)):
        return load_formal_principal(db, user_id)
    with patch.object(app, 'dependency_overrides', {get_db: database, get_formal_principal: principal,
            get_settings: lambda: settings, get_formal_file_storage_adapter: lambda: storage}):
        # The native gate already verifies actual production DB startup. Do not
        # start the app's unrelated bootstrap/worker lifecycle in this fixture.
        client = TestClient(app, raise_server_exceptions=False)
        try:
            yield client
        finally:
            client.close()


def checked(response, status=200):
    assert response.status_code == status, (response.status_code, response.text)
    assert 'no-store' in response.headers['cache-control']
    assert response.headers['referrer-policy'] == 'no-referrer'
    return response.json()


def run(owner, api, *, original, source, directory):
    region = UUID(source['owner_org_id'])
    with Session(owner) as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261230_0181'
        roles = {r.code: r for r in db.scalars(select(Role))}
        receiver = db.get(User, original['receiverUserId'])
        existing = db.scalar(select(RoleAssignment).where(RoleAssignment.user_id == receiver.id,
            RoleAssignment.role_id == roles['provincial_manager'].id,
            RoleAssignment.scope_type == 'organization', RoleAssignment.scope_id == str(region),
            RoleAssignment.status == 'active', RoleAssignment.revoked_at.is_(None), RoleAssignment.valid_to.is_(None)))
        if existing is None:
            assign(db, receiver, roles['provincial_manager'], scope_type='organization', scope_id=str(region))
        reviewer, _ = make_user(db, db.get(Organization, region), name='Synthetic HTTP independent reviewer')
        assign(db, reviewer, roles['provincial_manager'], scope_type='organization', scope_id=str(region))
        users = {'requester': receiver.id, 'regional': reviewer.id, 'headquarters': original['administratorUserId']}
        grants = {}
        for action in sorted(set(ACTIONS.values())):
            role = roles['admin' if action in ('review_return_condition_headquarters', 'cancel_return_condition_approval') else 'provincial_manager']
            grant = db.scalars(select(RolePermission).join(Permission).where(RolePermission.role_id == role.id,
                Permission.resource == 'stock_operation', Permission.action == action, Permission.field_code == '')).one()
            assert grant.effect == 'allow', 'missing default action grant: ' + action
            grants[action] = grant.id
        db.commit()
    assert len(set(users.values())) == 3
    storage = FakeStorage(); inbound = source['selection']['inbound_line_id']
    retained = []; actions = []; sealed = []; evidence_files = []
    with Session(api) as db:
        receipt = str(db.get(StockOperationReturnInbound, UUID(original['inboundId'])).receipt_id)
        sku = db.get(FormalMaterial, UUID(source['material_id'])).sku_code
        people = {name: str(load_formal_principal(db, user).person_id) for name, user in users.items()}

    def request(user, method, path, body=None, status=200, headers=None):
        with http(api, users[user], storage) as client:
            return checked(client.request(method, path, json=body, headers=headers or {}), status)

    def upload(user):
        key = uuid4().hex
        item = request(user, 'POST', FILES + '/upload-intents', dict(purpose='return_condition_evidence',
            original_filename='原生HTTP核验.jpg', size_bytes=128, mime_type='image/jpeg', sha256='a'*64),
            status=201, headers={'Idempotency-Key': key, 'X-Request-ID': key})
        with Session(api) as db:
            storage.materialize(db.get(FileObject, UUID(item['file_id'])))
        done = request(user, 'POST', FILES + '/' + item['file_id'] + '/complete',
            headers={'X-Request-ID': uuid4().hex})
        assert done['purpose'] == item['purpose'] == 'return_condition_evidence'
        return item['file_id']

    def recovery(user, body):
        return dict(operator_person_id=people[user], original=body)

    def headers(body):
        return {'X-Request-ID': body['request_id'], 'Idempotency-Key': body['idempotency_key']}

    def submit_body():
        file = upload('requester')
        before = snapshot(owner)
        preview = request('requester', 'GET', BASE + '/sources/' + inbound)
        assert snapshot(owner) == before and preview['posting_allowed'] is False
        serials = [dict(serial_id=s['serial_id'], sku_code=sku, serial_no=s['serial_no'], qr_code=s['qr_code'])
            for s in preview['serials'] if s['claimable_for_correction']]
        quantity = str(len(serials)) if serials else '0.025'
        assert 0 < Decimal(quantity) <= Decimal(preview['claimable_quantity'])
        return dict(action='submit_return_condition', inbound_line_id=inbound,
            expected_source_hash=preview['expected_source_hash'], quantity=quantity,
            serial_verifications=serials, evidence_file_ids=[file], reason='原生HTTP完整成色办理',
            request_id=uuid4().hex, idempotency_key=uuid4().hex)

    def decide_body(previous, kind, user, scans):
        body = dict(action=kind, case_id=previous['case_id'], expected_event_id=previous['event_id'],
            expected_event_hash=previous['request_hash'], reason='原生HTTP独立决定与结算',
            evidence_file_ids=[upload(user)] if kind in ('supplement', 'verify_region') else [],
            request_id=uuid4().hex, idempotency_key=uuid4().hex)
        if kind in ('execute', 'release'):
            body['serial_verifications'] = scans
        return body

    def commit(user, body):
        before = snapshot(owner)
        missing = request(user, 'POST', BASE + '/request-lookup', recovery(user, body), headers=headers(body))
        assert missing['request_state'] == 'unknown' and missing['retry_allowed'] is False
        assert snapshot(owner) == before
        result = request(user, 'POST', BASE + '/commands', body, headers=headers(body))
        assert result['actor_person_id'] == people[user] and result['request_id'] == body['request_id']
        assert result['action'] == ('submit' if body['action'] == 'submit_return_condition' else body['action'])
        retained.append((user, body, result)); actions.append(result['action'])
        # New sessions have only the exact original command, not an in-memory result.
        observed = snapshot(owner)
        found = request(user, 'POST', BASE + '/request-lookup', recovery(user, body), headers=headers(body))
        assert found['request_state'] == 'found' and found['result'] == result and found['retry_allowed'] is False
        assert snapshot(owner) == observed
        for file in body['evidence_file_ids']:
            signed = request('headquarters', 'GET', FILES + '/' + file + '/download-intent',
                headers={'X-Request-ID': uuid4().hex})
            assert signed['file_id'] == file and signed['purpose'] == 'return_condition_evidence'
            assert signed['download']['method'] == 'GET'
            evidence_files.append(file)
        print('condition HTTP actual COMMIT ' + result['action'] + ' PASS', flush=True)
        return result

    def close(user, body):
        before = stock(owner)
        value = request(user, 'POST', BASE + '/request-seal', recovery(user, body), headers=headers(body))
        assert value['request_state'] == 'sealed' and value['retry_allowed'] is False
        assert stock(owner) == before
        assert request(user, 'POST', BASE + '/request-lookup', recovery(user, body), headers=headers(body)) == value
        after = snapshot(owner)
        request(user, 'POST', BASE + '/commands', body, status=409, headers=headers(body))
        assert snapshot(owner) == after
        sealed.append((user, body, value))

    paths = (
        (('return_evidence','regional'), ('supplement','requester'), ('verify_region','regional'),
         ('return_region','headquarters'), ('verify_region','regional'), ('approve_hq','headquarters'),
         ('cancel_approved','headquarters'), ('release','requester')),
        (('reject_region','regional'), ('release','requester')),
        (('verify_region','regional'), ('reject_hq','headquarters'), ('release','requester')),
        (('withdraw','requester'), ('release','requester')),
        (('verify_region','regional'), ('approve_hq','headquarters'), ('execute','requester')),
    )
    for index, path in enumerate(paths):
        body = submit_body(); scans = body['serial_verifications']
        if index == 0:
            # A missing original is explicitly closed, never automatically replayed.
            absent = dict(body, request_id=uuid4().hex, idempotency_key=uuid4().hex)
            close('requester', absent)
        if index == 0:
            before = snapshot(owner)
            request('requester', 'POST', BASE + '/commands', body, status=400,
                headers={'X-Request-ID': 'different-original', 'Idempotency-Key': body['idempotency_key']})
            assert snapshot(owner) == before
        result = commit('requester', body)
        if index == 0:
            inbox = request('regional', 'GET', BASE + '/inbox')
            assert inbox['person_id'] == people['regional'] and len(inbox['items']) == 1
            assert inbox['items'][0]['inbound_line_id'] == inbound
            close('requester', decide_body(result, 'withdraw', 'requester', scans))
        for kind, user in path:
            before_stock = stock(owner)
            body = decide_body(result, kind, user, scans)
            result = commit(user, body)
            if kind not in ('execute', 'release'):
                assert stock(owner) == before_stock, 'approval changed stock'
        before = snapshot(owner)
        history = request('requester', 'GET', BASE + '/history/' + inbound)
        assert history['events'][-1]['fact'] == result and history['current_stock_verified'] is False
        sources = request('requester', 'GET', BASE + '/receipts/' + receipt)
        assert sources['status'] == 'posted' and sources['items'] == [history]
        assert snapshot(owner) == before
    assert request('regional', 'GET', BASE + '/inbox')['items'] == []
    assert request('headquarters', 'GET', BASE + '/inbox?view=all')['items'] == [history]
    assert set(actions) == set(ACTIONS)
    assert history['held_quantity'] == '0.000'
    assert Decimal(history['corrected_quantity']) == Decimal(result['quantity'])
    assert result['status'] == 'executed' and result['stock_effect'] == 'status_change'
    # Current write denial cannot conceal previous results or bound evidence.
    with owner.begin() as db:
        db.execute(text("UPDATE role_permissions SET effect='deny' WHERE id=:id"), {'id': grants['submit_return_condition']})
    try:
        user, body, result = retained[0]
        before = snapshot(owner)
        found = request(user, 'POST', BASE + '/request-lookup', recovery(user, body), headers=headers(body))
        assert found['result'] == result and found['current_case_status'] == 'released_cancelled'
        request(user, 'POST', BASE + '/commands', body, status=403, headers=headers(body))
        assert snapshot(owner) == before
        signed = request(user, 'GET', FILES + '/' + evidence_files[0] + '/download-intent', headers={'X-Request-ID': uuid4().hex})
        assert signed['file_id'] == evidence_files[0]
    finally:
        with owner.begin() as db:
            db.execute(text("UPDATE role_permissions SET effect='allow' WHERE id=:id"), {'id': grants['submit_return_condition']})
    return dict(passed=True, actualFullApplicationHttp=True, actualApiRoleCommit=True,
        formalDefaultGrants=True, actions=sorted(set(actions)), commandCount=len(retained),
        exactOriginalRecovery=True, closedMissingRequests=len(sealed), lateClosedWritesRefused=True,
        fileUploadCompleteAndBoundDownload=True, historyAndReceiptReadOnly=True,
        writeRevokedRecovery=True, authentication='injected_identity_with_real_current_principal',
        storage='FakeStorage', productionAcceptance=False)
