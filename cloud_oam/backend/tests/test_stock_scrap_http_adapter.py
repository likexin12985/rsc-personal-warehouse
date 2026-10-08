"""HTTP transaction/permission boundary; actual PG business proof is separate."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.dependencies import get_formal_principal
from app.routers import formal_stock_scrap as router
from app.formal_services.stock_scrap import bound_commands, request_lookup, recovery_lookup, request_seals
from test_loss_correction_http_adapter import fact as inverse_fact, request as inverse_request


FLOWS = {
    'original': ('originals', 'dispose_loss', 'original'),
    'correction': ('corrections', 'correct_loss', 'correct'),
    'apply': ('recovery/applications', 'apply_scrap_recovery', 'review'),
    'regional': ('recovery/regional-reviews', 'review_scrap_recovery_regional', 'review'),
    'headquarters': ('recovery/headquarters-reviews', 'review_scrap_recovery_headquarters', 'review'),
    'execute': ('recovery/executions', 'execute_scrap_recovery', 'recover'),
}


def identifier():
    return str(uuid4())


def request(kind):
    coordinates = dict(request_id='synthetic-request-1', idempotency_key='PRIVATE-original-key-1')
    if kind in ('original', 'correction'):
        source = dict(kind=kind, expected_submission_plan_hash='1'*64)
        if kind == 'original':
            source.update(headquarters_decision_id=identifier(), expected_headquarters_review_hash='2'*64)
        else:
            source.update(root_disposition_id=identifier(), expected_root_request_hash='2'*64,
                reversal_id=identifier(), expected_reversal_hash='3'*64,
                correction_decision_id=identifier(), expected_correction_decision_hash='4'*64)
        return dict(**coordinates, source=source, execution_reason='Synthetic scrap reason',
                    evidence_file_ids=[identifier()], expected_plan_hash='5'*64)
    source = dict(scrap_line_id=identifier(), expected_scrap_request_hash='1'*64)
    common = dict(**coordinates, source=source, reason='Synthetic recovery reason')
    if kind == 'apply':
        return dict(common, action='apply_scrap_recovery', evidence_file_ids=[identifier()])
    common.update(recovery_request_id=identifier(), expected_request_hash='2'*64)
    if kind == 'regional':
        return dict(common, action='review_scrap_recovery_region', decision='verified')
    if kind == 'headquarters':
        return dict(common, action='review_scrap_recovery_headquarters', decision='approve',
                    regional_review_id=identifier(), expected_regional_hash='3'*64)
    return dict(common, action='execute_scrap_recovery', headquarters_review_id=identifier(),
                expected_headquarters_hash='4'*64, expected_plan_hash='5'*64)


def fact(kind, body, person):
    common = dict(request_id=body['request_id'], request_hash='7'*64)
    if kind in ('original', 'correction'):
        return dict(common, scrap_operation_id=identifier(), scrap_line_id=identifier(), source_kind=kind,
            root_disposition_id=body['source'].get('root_disposition_id', identifier()),
            correction_execution_id=identifier() if kind == 'correction' else None,
            posting_transaction_id=identifier(), posting_movement_id=identifier(), quantity='1.000',
            source_account_id=identifier(), target_account_id=None, status='posted',
            stock_effect='removed_from_managed_assets', plan_hash=body['expected_plan_hash'])
    if kind == 'execute':
        value = inverse_fact('inverses', inverse_request('inverses'))
        return dict(value, **common, source_account_id=None, actor_person_id=person)
    fid = identifier()
    return dict(common, fact_id=fid, recovery_request_id=fid if kind == 'apply' else body['recovery_request_id'],
        scrap_line_id=body['source']['scrap_line_id'], stage=kind,
        status={'apply': 'awaiting_regional', 'regional': 'awaiting_headquarters', 'headquarters': 'approved_pending_execution'}[kind],
        stock_effect='none', actor_person_id=person, authorization_version=1, reason=body['reason'],
        decision=body.get('decision'), regional_review_id=body.get('regional_review_id'))


@pytest.fixture
def harness():
    app = FastAPI(); app.include_router(router.router)
    calls = []
    class DB:
        commits = rollbacks = 0
        fail_commit = False
        def commit(self):
            self.commits += 1
            if self.fail_commit:
                raise OperationalError('PRIVATE-SQL', {}, Exception('PRIVATE-DB'))
        def rollback(self):
            self.rollbacks += 1
    db = DB(); permissions = {row[1] for row in FLOWS.values()} | {'read'}
    def allows(database, resource, action, **kwargs):
        assert database is db and resource == 'stock_operation'
        calls.append(action)
        return action in permissions
    principal = SimpleNamespace(user_id='synthetic-user', person_id=uuid4(), allows=allows)
    app.dependency_overrides = {get_db: lambda: db, get_formal_principal: lambda: principal}
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, db=db, permissions=permissions, principal=principal, calls=calls)


def url(kind, mode='write'):
    return '/scraps/' + FLOWS[kind][0] + ('' if mode == 'write' else '/request-' + mode)


def public(reply, status):
    assert reply.status_code == status, reply.text
    assert reply.headers['cache-control'] == 'private, no-store'
    assert 'PRIVATE-' not in reply.text and 'command_jsonb' not in reply.text
    return reply.json()


@pytest.mark.parametrize('kind', FLOWS)
@pytest.mark.parametrize('fault', ['none', 'commit', 'database', 'invalid_output', 'wrong_request'])
def test_single_commit_after_validated_result(harness, monkeypatch, kind, fault):
    body = request(kind); result = fact(kind, body, str(harness.principal.person_id))
    def service(db, *, actor, request):
        assert db is harness.db and actor is harness.principal
        assert request.model_dump(mode='json') == body
        if fault == 'database':
            raise OperationalError('PRIVATE-SQL', {}, Exception('PRIVATE-DB'))
        if fault == 'invalid_output':
            return dict(result, command_jsonb={'secret': 'PRIVATE-key'})
        if fault == 'wrong_request':
            return dict(result, request_id='different-original-request')
        return result
    monkeypatch.setattr(bound_commands, FLOWS[kind][2], service)
    harness.db.fail_commit = fault == 'commit'
    public(harness.client.post(url(kind), json=body), 200 if fault == 'none' else 503)
    assert harness.db.commits == int(fault in ('none', 'commit'))
    assert harness.db.rollbacks == int(fault != 'none')


def observed(kind, body, person, state):
    answer = dict(request_state=state, retry_allowed=False, request_id=body['request_id'],
        request_hash='7'*64, result_scope='unconfirmed_request', result=None)
    if state == 'found':
        answer.update(result_scope='historical_original_outcome', result=fact(kind, body, person))
    if state == 'sealed':
        answer.update(result_scope='closed_original_request', seal=dict(seal_id=identifier(), kind=kind,
            loss_operation_id=identifier(), loss_line_id=identifier(),
            root_disposition_id=None if kind == 'original' else identifier(),
            sealed_at=datetime.now(timezone.utc).isoformat(), stock_effect='none'))
    return answer


@pytest.mark.parametrize('kind', FLOWS)
@pytest.mark.parametrize('state', ['not_found', 'found', 'sealed', 'database'])
def test_lookup_needs_only_read_and_never_replays(harness, monkeypatch, kind, state):
    body = request(kind); person = str(harness.principal.person_id)
    harness.permissions.clear(); harness.permissions.add('read')
    def forbidden(*args, **kwargs):
        raise AssertionError('read-only lookup invoked a write or preview')
    for name in ('original', 'correct', 'review', 'recover'):
        monkeypatch.setattr(bound_commands, name, forbidden)
    monkeypatch.setattr(request_seals, 'seal', forbidden)
    monkeypatch.setattr(router.stock_scrap_plan, 'prepare', forbidden)
    monkeypatch.setattr(router.recovery_plan, 'prepare', forbidden)
    def lookup(db, *, actor, request):
        assert request.original.model_dump(mode='json') == body
        if state == 'database':
            raise OperationalError('PRIVATE-SQL', {}, Exception('PRIVATE-DB'))
        return observed(kind, body, person, state)
    monkeypatch.setattr(request_lookup if kind in ('original', 'correction') else recovery_lookup, 'lookup', lookup)
    result = public(harness.client.post(url(kind, 'lookup'), json=dict(operator_person_id=person, original=body)),
                    503 if state == 'database' else 200)
    if state != 'database':
        assert result['request_state'] == state and result['retry_allowed'] is False
    assert harness.calls == ['read'] and harness.db.commits == harness.db.rollbacks == 0


@pytest.mark.parametrize('kind', FLOWS)
@pytest.mark.parametrize('state', ['found', 'sealed', 'not_found', 'wrong_kind'])
def test_seal_requires_closed_outcome(harness, monkeypatch, kind, state):
    body = request(kind); person = str(harness.principal.person_id)
    answer = observed(kind, body, person, 'sealed' if state == 'wrong_kind' else state)
    if state == 'wrong_kind':
        answer['seal']['kind'] = 'regional' if kind != 'regional' else 'apply'
    monkeypatch.setattr(request_seals, 'seal', lambda *args, **kwargs: answer)
    valid = state in ('found', 'sealed')
    public(harness.client.post(url(kind, 'seal'), json=dict(operator_person_id=person, original=body)), 200 if valid else 503)
    assert harness.db.commits == int(valid) and harness.db.rollbacks == int(not valid)


@pytest.mark.parametrize('kind', FLOWS)
@pytest.mark.parametrize('mode,fault', [(mode, fault)
    for mode in ('write', 'lookup', 'seal') for fault in ('permission', 'header', 'operator', 'stage')
    if (mode, fault) != ('write', 'operator')])
def test_admission_refuses_before_service(harness, monkeypatch, kind, mode, fault):
    body = request(kind); person = str(harness.principal.person_id)
    def forbidden(*args, **kwargs):
        raise AssertionError('invalid admission reached service')
    for name in ('original', 'correct', 'review', 'recover'):
        monkeypatch.setattr(bound_commands, name, forbidden)
    for module in (request_seals, request_lookup, recovery_lookup):
        monkeypatch.setattr(module, 'seal' if module is request_seals else 'lookup', forbidden)
    headers = {}; status = 403 if fault in ('permission', 'operator') else 400
    if fault == 'permission':
        harness.permissions.remove('read' if mode == 'lookup' else FLOWS[kind][1])
    elif fault == 'operator':
        person = identifier()
    elif fault == 'header':
        headers['Idempotency-Key'] = 'different-original-key'
    else:
        if kind in ('original', 'correction'):
            body = request('correction' if kind == 'original' else 'original')
        else:
            body = request('regional' if kind != 'regional' else 'apply')
            if mode == 'write':
                status = 422  # A direct stage endpoint has its own strict command type.
    payload = body if mode == 'write' else dict(operator_person_id=person, original=body)
    reply = harness.client.post(url(kind, mode), json=payload, headers=headers)
    assert reply.status_code == status, reply.text
    assert 'PRIVATE-' not in reply.text
    assert harness.db.commits == harness.db.rollbacks == 0


@pytest.mark.parametrize('kind', ['original', 'correction', 'execute'])
def test_preview_projects_only_public_fields_without_commit(harness, monkeypatch, kind):
    body = request(kind)
    for name in ('request_id', 'idempotency_key', 'expected_plan_hash', 'action'):
        body.pop(name, None)
    document = dict(stock_effect='none', quantity='1.000', serial_ids=[],
        intent={'private': 'PRIVATE-intent'}, frozen_holds_before={'private': 'PRIVATE-holds'},
        source_account_id=None if kind == 'execute' else identifier(),
        target_account_id=identifier() if kind == 'execute' else None)
    if kind == 'execute':
        document.update(root_disposition_id=identifier(), original_execution_id=identifier(),
            original_transaction_id=identifier(), original_movement_id=identifier(), target_condition='new')
    else:
        document.update(operation_id=identifier(), line_id=identifier(), decision_id=identifier(),
            predecessor_reversal_id=identifier() if kind == 'correction' else None, source_condition='new')
    module = router.recovery_plan if kind == 'execute' else router.stock_scrap_plan
    monkeypatch.setattr(module, 'prepare', lambda *args, **kwargs: SimpleNamespace(
        document=document, plan_hash='5'*64, checked_at=datetime.now(timezone.utc)))
    answer = public(harness.client.post(url(kind)+'/preview', json=body), 200)
    assert answer['planning_status'] == 'preview_only' and answer['stock_effect'] == 'none'
    assert 'intent' not in answer and 'frozen_holds_before' not in answer
    assert harness.db.commits == harness.db.rollbacks == 0


@pytest.mark.parametrize('kind', FLOWS)
@pytest.mark.parametrize('mode', ['write', 'lookup', 'seal'])
def test_invalid_private_body_is_never_reflected(harness, kind, mode):
    body = request(kind)
    body['unexpected_internal_field'] = 'PRIVATE-sensitive-value'
    payload = body if mode == 'write' else dict(operator_person_id=str(harness.principal.person_id), original=body)
    answer = public(harness.client.post(url(kind, mode), json=payload), 422)
    assert answer['detail']['code'] == 'stock_scrap_request_invalid'
    assert harness.db.commits == harness.db.rollbacks == 0


def test_complete_explicit_route_set(harness):
    actual = {(r.path, tuple(sorted(r.methods))) for r in harness.client.app.routes if r.path.startswith('/scraps')}
    expected = {(url(kind, mode), ('POST',)) for kind in FLOWS for mode in ('write', 'lookup', 'seal')}
    expected |= {(url(kind)+'/preview', ('POST',)) for kind in ('original', 'correction', 'execute')}
    expected |= {('/scraps/recovery/sources', ('GET',)), ('/scraps/recovery/sources/{scrap_line_id}', ('GET',))}
    assert actual == expected


def test_public_models_accept_the_committed_native_client_fixture():
    import json
    from pathlib import Path
    from app import stock_scrap_http_schemas as schemas
    cloud = next(p for p in Path(__file__).resolve().parents if (p/'frontend/src').is_dir())
    value = json.loads((cloud/'frontend/src/test-fixtures/stock-scrap/committed-quantity-results.json').read_text())
    classes = dict(original=schemas.ScrapFact, correction=schemas.ScrapFact,
        apply=schemas.RecoveryApplyFact, regional=schemas.RecoveryRegionalFact,
        headquarters=schemas.RecoveryHeadquartersFact, execute=schemas.RecoveryPostingFact)
    assert len(value['samples']) == 10
    for sample in value['samples']:
        assert classes[sample['kind']].model_validate(sample['fact']).model_dump(mode='json') == sample['fact']
