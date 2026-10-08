"""HTTP transport/transaction faults; PG business history is tested separately."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.dependencies import get_formal_principal
from app.routers import formal_return_conditions as api
from app.return_condition_http_schemas import ConditionCommand
from app.return_condition_schema import TRANSITIONS


ACTIONS = tuple(api.ACTIONS)
PREFIX = '/return-condition-corrections'


def command(action):
    common = dict(action=action, request_id='condition-http-original',
        idempotency_key='PRIVATE-original-key', reason='本次现场核验', evidence_file_ids=[str(uuid4())])
    if action == 'submit':
        return dict(common, action='submit_return_condition', inbound_line_id=str(uuid4()),
            expected_source_hash='1'*64, quantity='1.000', serial_verifications=[])
    result = dict(common, case_id=str(uuid4()), expected_event_id=str(uuid4()), expected_event_hash='2'*64)
    if action in ('execute', 'release'):
        result['serial_verifications'] = []
    return result


def fact(body, principal):
    kind = 'submit' if body['action'] == 'submit_return_condition' else body['action']
    status = next(after for action, _, after in TRANSITIONS if action == kind)
    # Real inventory/history facts are independently verified by service gates.
    # This deliberately synthetic result tests HTTP binding and fault handling.
    posted = kind in ('submit', 'execute', 'release')
    return dict(schema_version='condition_result/1', case_id=body.get('case_id', str(uuid4())),
        event_id=str(uuid4()), inbound_line_id=body.get('inbound_line_id', str(uuid4())),
        action=kind, status=status, quantity='1.000', actor_user_id=principal.user_id,
        actor_person_id=str(principal.person_id), authorization_version=1,
        request_id=body['request_id'], request_hash='3'*64, plan_hash='4'*64,
        posting_transaction_id=str(uuid4()) if posted else None,
        posting_movement_id=str(uuid4()) if posted else None,
        stock_effect={'submit': 'freeze', 'execute': 'status_change', 'release': 'unfreeze'}.get(kind, 'none'),
        reason=body['reason'])


def outcome(body, principal, state):
    original = api._original(TypeAdapter(ConditionCommand).validate_python(body))
    result = dict(request_state=state, result_scope='historical_original_outcome', result=None,
        absence_sealed=False, retry_allowed=False, current_stock_verified=False, observed_ledger_cursor=12)
    if state == 'found':
        historical = fact(body, principal)
        result.update(result=historical, current_case_status='executed',
            original_input_hash=api._canonical_hash(original))
    elif state == 'sealed':
        kind = 'submit' if body['action'] == 'submit_return_condition' else body['action']
        raw_seal = dict(id=str(uuid4()), kind=kind, request_id=body['request_id'],
            inbound_line_id=body.get('inbound_line_id', str(uuid4())),
            actor_user_id=principal.user_id, actor_person_id=str(principal.person_id),
            command_jsonb=original, request_hash=api._canonical_hash(original),
            created_at=datetime.now(timezone.utc).isoformat(), request_state='sealed',
            retry_allowed=False, stock_effect='none', history_hash='5'*64)
        if kind != 'submit':
            raw_seal.update(case_id=body['case_id'], expected_event_id=body['expected_event_id'])
        result.update(result_scope='closed_original_request', absence_sealed=True, seal=raw_seal,
            original_input_hash=api._canonical_hash(original), stock_effect='none')
        if kind != 'submit':
            result['original_preflight_verified'] = False
    return result


@pytest.fixture
def harness():
    app = FastAPI(); app.include_router(api.router)
    class DB:
        commits = rollbacks = 0
        fail_commit = False
        def commit(self):
            self.commits += 1
            if self.fail_commit:
                raise OperationalError('PRIVATE-SQL', {}, Exception('PRIVATE-DB'))
        def rollback(self):
            self.rollbacks += 1
    db = DB(); permissions = set(api.ACTIONS.values()) | {'read'}; permission_calls = []
    def allows(database, resource, action):
        assert database is db and resource == 'stock_operation'
        permission_calls.append(action)
        return action in permissions
    principal = SimpleNamespace(user_id='condition-http-user', person_id=uuid4(), allows=allows)
    app.dependency_overrides = {get_db: lambda: db, get_formal_principal: lambda: principal}
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, db=db, principal=principal,
            permissions=permissions, permission_calls=permission_calls)


def install(monkeypatch, callback, mode):
    targets = {
        'write': ((api.submission, 'submit'), (api.decisions, 'decide'), (api.settlement, 'settle')),
        'lookup': ((api.recovery, 'lookup'), (api.action_recovery, 'lookup')),
        'seal': ((api.seals, 'seal'), (api.action_seals, 'seal')),
    }
    def forbidden(*args, **kwargs):
        raise AssertionError('HTTP adapter crossed from selected operation into another operation')
    for kind, functions in targets.items():
        for module, name in functions:
            monkeypatch.setattr(module, name, callback if kind == mode else forbidden)


def invoke(harness, body, mode, **kwargs):
    endpoint = '/commands' if mode == 'write' else '/request-' + mode
    payload = body if mode == 'write' else dict(operator_person_id=str(harness.principal.person_id), original=body)
    return harness.client.post(PREFIX + endpoint, json=payload, **kwargs)


def public(response, status):
    assert response.status_code == status, response.text
    assert response.headers['cache-control'] == 'private, no-store'
    assert response.headers['referrer-policy'] == 'no-referrer'
    assert 'PRIVATE-' not in response.text
    assert 'command_jsonb' not in response.text and 'idempotency_key' not in response.text
    return response.json()


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('fault', ['none', 'database', 'commit', 'request', 'actor', 'source', 'effect', 'private_field'])
def test_all_commands_bind_result_before_single_commit(harness, monkeypatch, action, fault):
    body = command(action); expected = fact(body, harness.principal)
    def service(db, *, actor, request):
        assert db is harness.db and actor is harness.principal
        assert request.model_dump(mode='json') == body
        if fault == 'database':
            raise OperationalError('PRIVATE-SQL', {}, Exception('PRIVATE-DB'))
        result = dict(expected)
        if fault == 'request': result['request_id'] = 'unrelated-request'
        if fault == 'actor': result['actor_person_id'] = str(uuid4())
        if fault == 'source': result['inbound_line_id' if action == 'submit' else 'case_id'] = str(uuid4())
        if fault == 'effect': result['stock_effect'] = 'freeze' if action != 'submit' else 'none'
        if fault == 'private_field': result['command_jsonb'] = {'key': 'PRIVATE-key'}
        return result
    install(monkeypatch, service, 'write'); harness.db.fail_commit = fault == 'commit'
    public(invoke(harness, body, 'write'), 200 if fault == 'none' else 503)
    assert harness.db.commits == int(fault in ('none', 'commit'))
    assert harness.db.rollbacks == int(fault != 'none')
    assert harness.permission_calls == [api.ACTIONS[action]]


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('state', ['unknown', 'found', 'sealed'])
def test_read_needs_only_read_and_never_retries(harness, monkeypatch, action, state):
    body = command(action); harness.permissions.clear(); harness.permissions.add('read')
    install(monkeypatch, lambda *args, **kwargs: outcome(body, harness.principal, state), 'lookup')
    result = public(invoke(harness, body, 'lookup'), 200)
    assert result['request_state'] == state and result['retry_allowed'] is False
    assert result['current_stock_verified'] is False
    assert harness.permission_calls == ['read']
    assert harness.db.commits == harness.db.rollbacks == 0


@pytest.mark.parametrize('action', ACTIONS)
@pytest.mark.parametrize('state', ['unknown', 'found', 'sealed', 'wrong_input', 'wrong_reference'])
def test_seal_only_commits_exact_found_or_closed_request(harness, monkeypatch, action, state):
    body = command(action)
    response = outcome(body, harness.principal, 'sealed' if state.startswith('wrong') else state)
    if state == 'wrong_input': response['seal']['command_jsonb'] = {}
    if state == 'wrong_reference': response['seal']['inbound_line_id' if action == 'submit' else 'expected_event_id'] = str(uuid4())
    install(monkeypatch, lambda *args, **kwargs: response, 'seal')
    good = state in ('found', 'sealed')
    public(invoke(harness, body, 'seal'), 200 if good else 503)
    assert harness.db.commits == int(good) and harness.db.rollbacks == int(not good)


@pytest.mark.parametrize('mode', ['write', 'lookup', 'seal'])
@pytest.mark.parametrize('fault', ['permission', 'header', 'invalid', 'float', 'operator'])
def test_refuse_before_service(harness, monkeypatch, mode, fault):
    body = command('submit')
    def forbidden(*args, **kwargs): raise AssertionError('rejected request reached service')
    install(monkeypatch, forbidden, mode)
    headers = {}
    if fault == 'permission': harness.permissions.clear()
    if fault == 'header': headers['Idempotency-Key'] = 'PRIVATE-unrelated'
    if fault == 'invalid': body['action'] = 'PRIVATE-invalid-action'
    if fault == 'float': body['quantity'] = 1.0
    if fault == 'operator':
        body['operator_person_id'] = str(uuid4())  # Undeclared actor input is rejected.
    expected = {'permission': 403, 'header': 400}.get(fault, 422)
    public(invoke(harness, body, mode, headers=headers), expected)
    assert harness.db.commits == harness.db.rollbacks == 0


def test_wrapper_cannot_select_another_actor(harness, monkeypatch):
    install(monkeypatch, lambda *args, **kwargs: pytest.fail('wrong actor reached lookup'), 'lookup')
    response = harness.client.post(PREFIX+'/request-lookup', json={
        'operator_person_id': str(uuid4()), 'original': command('submit')})
    public(response, 403)
    assert harness.db.commits == harness.db.rollbacks == 0


def test_permission_database_failure_is_private_without_invoking_business(harness, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OperationalError('PRIVATE-SQL', {}, Exception('PRIVATE-DB'))
    harness.principal.allows = unavailable
    install(monkeypatch, lambda *args, **kwargs: pytest.fail('permission failure reached service'), 'write')
    result = public(invoke(harness, command('submit'), 'write'), 503)
    assert result['detail']['code'] == 'return_condition_admission_unavailable'
    assert harness.db.commits == 0


def test_full_application_mounts_all_condition_routes():
    from app.main import app
    paths = {route.path for route in app.routes}
    prefix = '/api/v1/stock-operations/loss-reports' + PREFIX
    assert {prefix + suffix for suffix in ('/commands', '/request-lookup', '/request-seal',
        '/sources/{inbound_line_id}')} <= paths


@pytest.mark.parametrize('state', ['recorded_stock_retained', 'verified_condition_history', 'later_activity_requires_reconciliation'])
@pytest.mark.parametrize('tracking', ['none', 'serial'])
def test_source_preview_releases_locks_and_never_claims_unknown_as_zero(harness, monkeypatch, state, tracking):
    identifier = str(uuid4()); material = str(uuid4())
    doc = dict(stage='source_evidence_only', selection={'inbound_line_id': identifier,
        'root_disposition_id': str(uuid4())}, inbound_id=str(uuid4()), source_account_id=str(uuid4()),
        material_id=material, lot_id=None, location_id=str(uuid4()), custodian_person_id=str(harness.principal.person_id),
        recorded_condition='used', required_condition='damaged', source_status=state,
        historical_damaged_quantity='1.000', account_balance_quantity='2.000', claimable_quantity='1.000',
        policy_fingerprint=[[material, 'policy-id', tracking]], serials=[], observed_ledger_cursor=12,
        physical_verification_required=True, posting_allowed=False, current_projection_verified=True,
        submission_permission_checked=True, actor_user_id=harness.principal.user_id,
        actor_person_id=str(harness.principal.person_id))
    if tracking == 'serial':
        doc['serials'] = [dict(serial_id=str(uuid4()), serial_no='SN-1', qr_code='QR-1',
            retained_at_original_inbound=True, claimable_for_correction=True)]
    checked = SimpleNamespace(document=doc, evidence_hash='6'*64, checked_at=datetime.now(timezone.utc))
    def prepare(db, *, actor, inbound_line_id):
        assert str(inbound_line_id) == identifier and actor is harness.principal and db is harness.db
        return checked
    monkeypatch.setattr(api.preparation, 'inspect_submission_source', prepare)
    result = public(harness.client.get(PREFIX+'/sources/'+identifier), 200)
    assert result['claimable_quantity'] == (None if state == 'later_activity_requires_reconciliation' else '1.000')
    assert result['posting_allowed'] is False and result['expected_source_hash'] == checked.evidence_hash
    assert 'policy_fingerprint' not in result and 'actor_user_id' not in result
    assert harness.db.commits == 0 and harness.db.rollbacks == 1


def test_source_failure_rolls_back_and_does_not_echo_database(harness, monkeypatch):
    def prepare(*args, **kwargs):
        raise OperationalError('PRIVATE-SQL', {}, Exception('PRIVATE-DB'))
    monkeypatch.setattr(api.preparation, 'inspect_submission_source', prepare)
    public(harness.client.get(PREFIX+'/sources/'+str(uuid4())), 503)
    assert harness.db.commits == 0 and harness.db.rollbacks == 1


def test_full_application_authentication_and_framework_errors_are_private():
    from app.main import app
    # No lifespan/bootstrap and no identity override. A missing session must
    # be rejected before any business service or database fixture is needed.
    client = TestClient(app, raise_server_exceptions=False)
    prefix = '/api/v1/stock-operations/loss-reports' + PREFIX
    try:
        for suffix in ('/commands', '/request-lookup', '/request-seal'):
            response = client.post(prefix+suffix, json={})
            assert response.status_code == 401, response.text
            assert 'no-store' in response.headers['cache-control']
            assert response.headers['referrer-policy'] == 'no-referrer'
        for suffix, status in (('/commands', 405), ('/no-such-route', 404)):
            response = client.get(prefix+suffix)
            assert response.status_code == status, response.text
            assert 'no-store' in response.headers['cache-control']
    finally:
        client.close()
