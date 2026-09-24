"""Real Ed25519 directions, audited issuance, owner decisions and formal HTTP."""
import copy
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select

from app import inventory_control_handoff as service
from app import inventory_control_configuration as configuration
from app import dependencies
from app.config import get_settings
from app.database import get_db
from app.foundation_models import AuditEvent
from app.models import AuthSession, User
from app.routers import formal_control_configuration as router
from test_inventory_control_configuration import db, world, login, cli, prepared
from test_inventory_control_authority import history


@pytest.fixture
def keys(monkeypatch):
    api = Ed25519PrivateKey.generate(); owner = Ed25519PrivateKey.generate()
    original = get_settings()
    cfg = original.model_copy(update=dict(control_configuration_handoff_enabled=True,
        control_configuration_deployment_id=str(uuid4()),
        control_configuration_api_private_key=api.private_bytes_raw().hex(),
        control_configuration_api_public_key=api.public_key().public_bytes_raw().hex(),
        control_configuration_owner_private_key=owner.private_bytes_raw().hex(),
        control_configuration_owner_public_key=owner.public_key().public_bytes_raw().hex()))
    monkeypatch.setattr(service, 'get_settings', lambda: cfg)
    monkeypatch.setattr(configuration, 'get_settings', lambda: cfg)
    monkeypatch.setattr(dependencies, 'get_settings', lambda: cfg.model_copy(update={'environment':'production'}))
    return SimpleNamespace(api=api, owner=owner, settings=cfg)


def issue(db, world, login, purpose='preview', response=None):
    result = service.issue_control_handoff(db, access_token=login['access_token'], actor=world.actor,
        expected_version=login['expected_authorization_version'], command=login['command'], purpose=purpose, owner_response=response)
    db.commit(); return result


def owner(db, packet):
    response = service.run_owner_handoff(db, packet); db.commit(); return response


def inspect(db, world, login, response):
    return service.inspect_owner_response(db, access_token=login['access_token'], actor=world.actor,
        expected_version=login['expected_authorization_version'], command=login['command'], envelope=response)


def test_signed_preview_execute_and_status_do_not_carry_login_credentials(db, world, login, keys):
    packet = issue(db, world, login)
    review = owner(db, packet)
    assert inspect(db, world, login, review)['can_execute'] is True
    execute = issue(db, world, login, 'execute', review)
    result = owner(db, execute)
    assert result['payload']['result']['recorded'] is True
    before = history(db)
    assert owner(db, execute)['payload']['result'] == result['payload']['result']
    assert history(db) == before
    status = issue(db, world, login, 'status', result)
    recovered = owner(db, status)
    assert recovered['payload']['result'] == result['payload']['result']
    inspected = inspect(db, world, login, recovered)
    assert inspected['purpose'] == 'status' and inspected['can_execute'] is False
    assert not inspected['result']['start_ready'] and not inspected['result']['projection_published']
    receipt = db.get(AuditEvent, UUID(result['payload']['result']['execution_audit_event_id']))
    assert receipt.after_jsonb['handoff']['handoff_id'] == execute['payload']['handoff_id']
    serialized = json.dumps([packet, review, execute, result, status, recovered] + list(db.scalars(select(AuditEvent.after_jsonb))))
    assert login['access_token'] not in serialized
    assert keys.settings.jwt_secret not in serialized
    assert keys.settings.control_configuration_api_private_key not in serialized
    assert keys.settings.control_configuration_owner_private_key not in serialized


@pytest.mark.parametrize('change', ['command', 'purpose', 'signature', 'key_id', 'extra', 'target', 'database'])
def test_modified_or_wrong_target_request_cannot_execute(db, world, login, keys, change):
    packet = copy.deepcopy(issue(db, world, login))
    if change == 'command': packet['payload']['command']['reason'] = 'Changed request'
    elif change == 'purpose': packet['payload']['purpose'] = 'execute'
    elif change == 'signature': packet['signature'] = '0'*128
    elif change == 'key_id': packet['key_id'] = '0'*16
    elif change == 'extra': packet['access_token'] = 'unexpected'
    else:
        packet['payload']['deployment_id' if change == 'target' else 'database_id'] = str(uuid4())
        packet = service._sign(packet['payload'], keys.api, 'request')
    before = history(db)
    with pytest.raises(service.ControlHandoffError): owner(db, packet)
    db.commit(); assert history(db) == before


@pytest.mark.parametrize('change', ['revoked', 'authorization', 'expired'])
def test_current_access_and_expiry_checked_again_on_owner(db, world, login, keys, monkeypatch, change):
    packet = issue(db, world, login)
    if change == 'revoked': db.scalar(select(AuthSession)).revoked_at = service.authority._now(db)
    elif change == 'authorization': db.get(User, world.actor.user_id).authorization_version += 1
    else:
        from datetime import datetime, timezone
        monkeypatch.setattr(service.authority, '_now', lambda db: datetime.fromtimestamp(packet['payload']['expires_at'], timezone.utc))
    db.commit(); before = history(db)
    with pytest.raises((service.ControlHandoffError, configuration.ControlConfigurationError)):
        owner(db, packet)
    db.commit(); assert history(db) == before


def test_preview_capability_cannot_be_used_for_apply(db, world, login, keys):
    packet = issue(db, world, login); review = owner(db, packet); before = history(db)
    with pytest.raises(service.ControlHandoffError, match='command_mismatch'):
        configuration.execute_inventory_control_configuration(db, access_token=service.SignedControlRequest(packet),
            expected_authorization_version=world.actor.authorization_version, command=login['command'],
            review_sha256=review['payload']['result']['review_sha256'])
    db.commit(); assert history(db) == before


def test_response_direction_or_claimed_publication_is_refused(db, world, login, keys):
    packet = issue(db, world, login)
    with pytest.raises(service.ControlHandoffError, match='signature_invalid'): inspect(db, world, login, packet)
    response = owner(db, packet); response['payload']['result']['projection_published'] = True
    forged = service._sign(response['payload'], keys.owner, 'response')
    with pytest.raises(service.ControlHandoffError, match='invalid_response'): inspect(db, world, login, forged)


def test_expired_preview_can_only_request_status_under_current_access(db, world, login, keys, monkeypatch):
    from datetime import datetime, timezone
    response = owner(db, issue(db, world, login))
    monkeypatch.setattr(service.authority, '_now', lambda db: datetime.fromtimestamp(response['payload']['expires_at'], timezone.utc))
    assert not inspect(db, world, login, response)['can_execute']
    with pytest.raises(service.ControlHandoffError, match='review_expired'):
        issue(db, world, login, 'execute', response)
    packet = issue(db, world, login, 'status', response)
    assert owner(db, packet)['payload']['result']['recorded'] is False


def test_issuance_signature_failure_rolls_back_audit(db, world, login, keys, monkeypatch):
    before = history(db)
    monkeypatch.setattr(service, '_sign', lambda *a: (_ for _ in ()).throw(RuntimeError('signer failure')))
    with pytest.raises(RuntimeError, match='signer failure'): issue(db, world, login)
    db.commit(); assert history(db) == before


def test_owner_signature_failure_rolls_back_decision_and_execution_audits(db, world, login, keys, monkeypatch):
    review = owner(db, issue(db, world, login)); execute = issue(db, world, login, 'execute', review)
    before = history(db)
    monkeypatch.setattr(service, '_sign', lambda *a: (_ for _ in ()).throw(RuntimeError('owner signer failure')))
    with pytest.raises(RuntimeError, match='owner signer failure'): owner(db, execute)
    db.commit(); assert history(db) == before


def test_missing_or_reused_direction_keys_are_closed(keys, monkeypatch):
    cfg = keys.settings.model_copy(update={'control_configuration_owner_public_key': keys.settings.control_configuration_api_public_key})
    monkeypatch.setattr(service, 'get_settings', lambda: cfg)
    with pytest.raises(service.ControlHandoffError, match='unconfigured'): service._keys('api')
    cfg = cfg.model_copy(update={'control_configuration_handoff_enabled':False})
    with pytest.raises(service.ControlHandoffError, match='disabled'): service._keys('api')


@pytest.fixture
def http(db, keys):
    app = FastAPI(); app.include_router(router.router, prefix='/api'); router.install_validation_handler(app)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client: yield client


def test_formal_http_uses_real_login_and_audit_but_no_private_control_queries(db, world, login, keys, http):
    statements = []
    def capture(conn, cursor, sql, *rest): statements.append(sql)
    body = dict(command=login['command'].model_dump(mode='json'), expected_authorization_version=world.actor.authorization_version, purpose='preview')
    path = '/api/v1/inventory-control/configuration/handoffs'
    assert http.post(path, json=body).status_code == 401
    event.listen(db.get_bind(), 'before_cursor_execute', capture)
    try: result = http.post(path, json=body, headers={'Authorization':'Bearer '+login['access_token']})
    finally: event.remove(db.get_bind(), 'before_cursor_execute', capture)
    assert result.status_code == 200, result.text
    assert result.json()['decision_recorded'] is False and result.headers['Cache-Control'] == 'no-store'
    assert not any('inventory_control_' in sql for sql in statements)
    response = owner(db, result.json()['handoff'])
    checked = http.post('/api/v1/inventory-control/configuration/inspect-response',
        json=dict(command=body['command'], expected_authorization_version=world.actor.authorization_version, owner_response=response),
        headers={'Authorization':'Bearer '+login['access_token']})
    assert checked.status_code == 200 and checked.json()['can_execute'] is True


def test_http_validation_and_disabled_failures_do_not_echo_credentials(db, world, login, keys, http):
    path = '/api/v1/inventory-control/configuration/handoffs'
    headers = {'Authorization':'Bearer '+login['access_token']}
    result = http.post(path, json={'access_token':login['access_token']}, headers=headers)
    assert result.status_code == 422 and login['access_token'] not in result.text
    keys.settings.control_configuration_handoff_enabled = False
    result = http.post(path, json=dict(command=login['command'].model_dump(mode='json'),
        expected_authorization_version=world.actor.authorization_version, purpose='preview'), headers=headers)
    assert result.status_code == 503 and login['access_token'] not in result.text


@pytest.mark.parametrize('side', ['api', 'owner'])
def test_deployed_process_refuses_both_private_signing_authorities(keys, monkeypatch, side):
    cfg = keys.settings.model_copy(update={'environment':'production'})
    monkeypatch.setattr(service, 'get_settings', lambda: cfg)
    with pytest.raises(service.ControlHandoffError, match='unconfigured'): service._keys(side)
    setattr(cfg, 'control_configuration_' + ('owner' if side == 'api' else 'api') + '_private_key', '')
    assert service._keys(side)[2] == cfg.control_configuration_deployment_id


def test_cli_handoff_has_no_token_prompt_and_recovers_lost_commit_ack(db, world, login, keys, cli, monkeypatch, tmp_path, capsys):
    import sqlalchemy
    from sqlalchemy.orm import Session
    engine = db.get_bind()
    monkeypatch.setattr(sqlalchemy, 'create_engine', lambda *a, **kw: engine)
    monkeypatch.setattr(engine, 'dispose', lambda: None)
    monkeypatch.setattr(cli, '_connection_config', lambda: ('synthetic-dsn', 'synthetic-db'))
    monkeypatch.setattr(cli, '_database_preflight', lambda db, target: None)
    monkeypatch.setattr(cli, '_credential', lambda *a: pytest.fail('handoff must not ask for an access token'))
    path = tmp_path/'handoff.json'
    def run(packet):
        path.write_text(json.dumps(packet)); db.rollback()
        return cli.main(['--handoff-file', str(path)])
    assert run(issue(db, world, login)) == 0
    review = json.loads(capsys.readouterr().out)
    execute = issue(db, world, login, 'execute', review)
    commit = Session.commit
    def lost_ack(session):
        commit(session)
        raise RuntimeError('synthetic private commit error')
    monkeypatch.setattr(Session, 'commit', lost_ack)
    assert run(execute) == 3
    output = capsys.readouterr()
    assert not output.out and 'synthetic private' not in output.err
    assert json.loads(output.err)['next_action'] == 'read_exact_status'
    monkeypatch.setattr(Session, 'commit', commit)
    assert run(issue(db, world, login, 'status', review)) == 0
    recovered = json.loads(capsys.readouterr().out)
    assert inspect(db, world, login, recovered)['result']['recorded'] is True


def test_issuance_audit_alone_blocks_authority_migration_downgrade(db, world, login, keys):
    import runpy
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from test_inventory_control_authority import PATH
    issue(db, world, login); before = history(db)
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError, match='authority must be retained'):
            runpy.run_path(str(PATH))['downgrade']()
    assert history(db) == before


@pytest.mark.parametrize('change', ['extra', 'audit_id', 'created_at', 'valid_to', 'catalog'])
def test_malformed_signed_decision_cannot_be_displayed_as_recorded(db, world, login, keys, change):
    review = owner(db, issue(db, world, login))
    result = owner(db, issue(db, world, login, 'execute', review))
    decision = result['payload']['result']['decision']
    if change == 'extra': decision['access_token'] = 'unexpected'
    elif change == 'audit_id': decision['audit_event_id'] = 'invalid'
    elif change == 'created_at': decision['created_at'] = '2026-09-20'
    elif change == 'valid_to': decision['valid_to'] = 1
    else: decision['catalog_id'] = str(uuid4())
    with pytest.raises(service.ControlHandoffError):
        inspect(db, world, login, service._sign(result['payload'], keys.owner, 'response'))


def test_production_startup_validates_enabled_handoff_without_database_or_owner_key(keys):
    from test_startup_security_boundary import production_settings
    settings = production_settings(control_configuration_handoff_enabled=True)
    with pytest.raises(ValueError, match='separate configured API and owner'):
        settings.validate_api_startup()
    settings.control_configuration_deployment_id = keys.settings.control_configuration_deployment_id
    settings.control_configuration_api_private_key = keys.settings.control_configuration_api_private_key
    settings.control_configuration_owner_public_key = keys.settings.control_configuration_owner_public_key
    settings.validate_api_startup()
    settings.control_configuration_owner_private_key = keys.settings.control_configuration_owner_private_key
    with pytest.raises(ValueError, match='separate configured API and owner'):
        settings.validate_api_startup()


def test_protected_pg16_handoff_helper_import_and_parent_wiring():
    from pathlib import Path
    from pg16_inventory_control_handoff_gate import assert_inventory_control_handoff_gate
    assert callable(assert_inventory_control_handoff_gate)
    parent = (Path(__file__).parent/'pg16_inventory_control_authority_gate.py').read_text()
    assert 'assert_inventory_control_handoff_gate(owner_engine, api_engine, world)' in parent
