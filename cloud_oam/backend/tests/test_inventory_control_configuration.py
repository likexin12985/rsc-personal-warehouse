"""Real JWT signatures, live synthetic sessions, migrated facts and audit chains."""
from datetime import timedelta
import importlib.util
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import jwt
import pytest
from sqlalchemy import event, select, text

from app import inventory_control_configuration as service
from app.config import get_settings
from app.foundation_models import AuditEvent, FileObject, RolePermission, SourceSystem
from app.inventory_control_authority_models import InventoryControlAuthorityDecision as Decision
from app.models import AuthSession, User
from app.security import create_access_token
from test_inventory_control_authority import db, world, command, history, prepared, decide


@pytest.fixture
def login(db, world):
    now = service.authority._now(db)
    session = AuthSession(user_id=world.actor.user_id, refresh_token_hash=uuid4().hex + uuid4().hex,
        client_type='web', device_id=uuid4().hex, ip_address='hmac:1:' + 'a'*64,
        created_at=now-timedelta(seconds=1), expires_at=now+timedelta(hours=1))
    db.add(session); db.commit()
    return dict(access_token=create_access_token(world.actor.user_id, session.id),
        expected_authorization_version=world.actor.authorization_version, command=command(db, world))


def preview(db, login):
    return service.preview_inventory_control_configuration(db, **login)


def applied(db, login, reviewed=None):
    result = service.execute_inventory_control_configuration(db, **login,
        review_sha256=(reviewed or preview(db, login))['review_sha256'])
    db.commit()
    return result


def test_review_apply_and_exact_recovery_use_current_session_and_no_credentials_in_audit(db, world, login):
    before = history(db)
    reviewed = preview(db, login)
    assert history(db) == before
    assert reviewed['review']['actor_user_id'] == world.actor.user_id
    assert 'storage_key' not in reviewed['review']['evidence_file']
    assert service.read_inventory_control_configuration(db, **login, review_sha256=reviewed['review_sha256'])['recorded'] is False
    result = applied(db, login, reviewed)
    assert result['recorded'] and not result['projection_published'] and not result['start_ready']
    saved = history(db)
    assert applied(db, login, reviewed) == result
    assert service.read_inventory_control_configuration(db, **login, review_sha256=reviewed['review_sha256']) == result
    assert history(db) == saved
    row = db.get(Decision, UUID(result['decision']['decision_id']))
    assert service.authority._prove(db, row)
    events = list(db.scalars(select(AuditEvent)))
    assert len(events) == 4
    assert {e.action for e in events if e.aggregate_type == 'formal_file'} == {'file.upload_intent.created', 'file.upload_completed'}
    serialized = json.dumps([e.after_jsonb for e in events])
    assert login['access_token'] not in serialized and get_settings().jwt_secret not in serialized
    assert db.scalar(text('SELECT count(*) FROM inventory_transactions')) == 0
    assert db.scalar(text('SELECT count(*) FROM sync_runs')) == 0


@pytest.mark.parametrize('change', ['tampered', 'audience', 'missing_exp', 'missing_iat', 'expired', 'future',
    'long_lived', 'iat_bool', 'iat_string', 'exp_bool', 'sub_list', 'sid_bad', 'extra_claim', 'none_algorithm'])
def test_invalid_signed_session_credentials_do_not_touch_facts(db, login, change):
    claims = jwt.decode(login['access_token'], options={'verify_signature': False})
    if change == 'audience': claims['aud'] = 'other-purpose'
    elif change == 'missing_exp': del claims['exp']
    elif change == 'missing_iat': del claims['iat']
    elif change == 'expired': claims.update(iat=claims['iat']-1000, exp=claims['iat']-1)
    elif change == 'future': claims.update(iat=claims['iat']+100, exp=claims['exp']+100)
    elif change == 'long_lived': claims['exp'] += 1
    elif change == 'iat_bool': claims['iat'] = True
    elif change == 'iat_string': claims['iat'] = str(claims['iat'])
    elif change == 'exp_bool': claims['exp'] = True
    elif change == 'sub_list': claims['sub'] = [claims['sub']]
    elif change == 'sid_bad': claims['sid'] = 'not-a-session'
    elif change == 'extra_claim': claims['nbf'] = 1
    token = jwt.encode(claims, '' if change == 'none_algorithm' else
        'different-secret-at-least-thirty-two-characters' if change == 'tampered' else get_settings().jwt_secret,
        algorithm='none' if change == 'none_algorithm' else 'HS256')
    saved = history(db)
    with pytest.raises(service.ControlConfigurationError, match='authentication_required'):
        preview(db, dict(login, access_token=token))
    assert history(db) == saved


@pytest.mark.parametrize('change', ['revoked', 'expired', 'wrong_user', 'newer_session', 'mini', 'empty_device',
    'legacy_ip', 'old_ip_version', 'inactive_user', 'stale_version', 'permission_deny'])
def test_revoked_or_changed_current_access_refuses_apply_after_preview(db, world, login, change):
    reviewed = preview(db, login)
    session = db.scalar(select(AuthSession))
    now = service.authority._now(db)
    if change == 'revoked': session.revoked_at = now
    elif change == 'expired': session.expires_at = now
    elif change == 'wrong_user':
        # A valid other local user is not the token's session subject.
        from test_formal_access import make_organization, make_user
        other, _ = make_user(db, make_organization(db, name='Other synthetic HQ'), name='Other synthetic actor')
        session.user_id = other.id
    elif change == 'newer_session': session.created_at = now+timedelta(hours=1)
    elif change == 'mini': session.client_type = 'miniprogram'
    elif change == 'empty_device': session.device_id = ''
    elif change == 'legacy_ip': session.ip_address = '127.0.0.1'
    elif change == 'old_ip_version': session.ip_address = 'hmac:2:' + 'a'*64
    elif change == 'inactive_user': db.get(User, world.actor.user_id).is_active = False
    elif change == 'stale_version': db.get(User, world.actor.user_id).authorization_version += 1
    else: db.scalar(select(RolePermission)).effect = 'deny'
    db.commit(); before = history(db)
    with pytest.raises((service.ControlConfigurationError, service.authority.ControlAuthorityError)):
        applied(db, login, reviewed)
    db.commit(); assert history(db) == before


@pytest.mark.parametrize('change', ['reason', 'evidence', 'source', 'history', 'digest'])
def test_preview_binding_detects_changed_reviewed_evidence(db, world, login, change):
    reviewed = preview(db, login)
    if change == 'reason': login = dict(login, command=login['command'].model_copy(update={'reason':'Different requested reason'}))
    elif change == 'evidence': db.get(FileObject, world.file).size_bytes += 1
    elif change == 'source': db.get(SourceSystem, world.source).enabled = False
    elif change == 'history': decide(db, world)
    else: reviewed['review_sha256'] = 'b'*64
    db.commit(); before = history(db)
    error = service.authority.ControlAuthorityError if change == 'evidence' else service.ControlConfigurationError
    code = 'evidence_file_unavailable' if change == 'evidence' else 'review_changed'
    with pytest.raises(error, match=code):
        applied(db, login, reviewed)
    db.commit(); assert history(db) == before


def test_failed_execution_audit_rolls_back_domain_decision_and_its_audit(db, login, monkeypatch):
    reviewed = preview(db, login); before = history(db)
    def fail(*a, **kw): raise RuntimeError('synthetic execution audit failure')
    monkeypatch.setattr(service, 'append_audit_event', fail)
    with pytest.raises(RuntimeError, match='synthetic execution audit failure'):
        applied(db, login, reviewed)
    db.commit(); assert history(db) == before


def test_expiry_while_waiting_for_execution_audit_rolls_back_everything(db, login, monkeypatch):
    reviewed = preview(db, login); before = history(db)
    original = service.append_audit_event
    expires = jwt.decode(login['access_token'], options={'verify_signature': False})['exp']
    def append(*a, **kw):
        result = original(*a, **kw)
        from datetime import datetime, timezone
        monkeypatch.setattr(service.authority, '_now', lambda db: datetime.fromtimestamp(expires, timezone.utc))
        return result
    monkeypatch.setattr(service, 'append_audit_event', append)
    with pytest.raises(service.ControlConfigurationError, match='authentication_required'):
        applied(db, login, reviewed)
    db.commit(); assert history(db) == before


def test_caller_rollback_removes_successful_execution_and_both_audits(db, login):
    reviewed = preview(db, login); before = history(db)
    service.execute_inventory_control_configuration(db, **login, review_sha256=reviewed['review_sha256'])
    db.rollback(); assert history(db) == before


def test_status_does_not_invent_session_evidence_for_direct_domain_decision(db, world, login):
    reviewed = preview(db, login)
    decide(db, world, login['command']); before = history(db)
    with pytest.raises(service.ControlConfigurationError, match='execution_evidence_missing'):
        service.read_inventory_control_configuration(db, **login, review_sha256=reviewed['review_sha256'])
    assert history(db) == before


@pytest.mark.parametrize('operation', ['preview', 'apply', 'status'])
def test_pending_caller_edits_are_not_flushed_or_overwritten(db, world, login, operation):
    reviewed = preview(db, login); changed = db.get(FileObject, world.file); changed.size_bytes += 1
    fn = dict(preview=service.preview_inventory_control_configuration, apply=service.execute_inventory_control_configuration,
              status=service.read_inventory_control_configuration)[operation]
    with pytest.raises(service.ControlConfigurationError, match='requires_clean_session'):
        fn(db, **login, **({} if operation == 'preview' else {'review_sha256': reviewed['review_sha256']}))
    assert changed in db.dirty and changed.size_bytes == 101


def test_preview_and_status_issue_no_dml(db, login):
    statements = []
    def capture(conn, cursor, sql, *rest): statements.append(sql.lstrip().split()[0].upper())
    event.listen(db.get_bind(), 'before_cursor_execute', capture)
    try:
        reviewed = preview(db, login)
        service.read_inventory_control_configuration(db, **login, review_sha256=reviewed['review_sha256'])
    finally: event.remove(db.get_bind(), 'before_cursor_execute', capture)
    assert set(statements) <= {'SELECT'}


@pytest.fixture
def cli():
    path = Path(__file__).parents[2]/'scripts/configure_inventory_control.py'
    spec = importlib.util.spec_from_file_location('control_configuration_cli_test', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_cli_preview_apply_status_and_lost_commit_ack_use_exact_request(db, login, cli, tmp_path, monkeypatch, capsys):
    import sqlalchemy
    from sqlalchemy.orm import Session
    engine = db.get_bind()
    monkeypatch.setattr(sqlalchemy, 'create_engine', lambda *a, **kw: engine)
    monkeypatch.setattr(engine, 'dispose', lambda: None)
    monkeypatch.setattr(cli, '_connection_config', lambda: ('synthetic-dsn', 'synthetic-db'))
    monkeypatch.setattr(cli, '_database_preflight', lambda db, target: None)
    path = tmp_path/'command.json'
    document = dict(command=login['command'].model_dump(mode='json'),
                    expected_authorization_version=login['expected_authorization_version'])
    def run(mode):
        path.write_text(json.dumps(document))
        reader, writer = os.pipe()
        os.write(writer, login['access_token'].encode()); os.close(writer)
        try: return cli.main(['--mode', mode, '--command-file', str(path), '--access-token-fd', str(reader)])
        finally: os.close(reader)
    db.rollback()
    assert run('preview') == 0
    output = capsys.readouterr(); reviewed = json.loads(output.out)
    document['review_sha256'] = reviewed['review_sha256']
    original_commit = Session.commit
    def lost_ack(session):
        original_commit(session)
        raise RuntimeError('synthetic lost acknowledgement with private credential ' + login['access_token'])
    monkeypatch.setattr(Session, 'commit', lost_ack)
    assert run('apply') == 3
    output = capsys.readouterr()
    assert not output.out and login['access_token'] not in output.err
    assert json.loads(output.err)['next_action'] == 'read_exact_status'
    monkeypatch.setattr(Session, 'commit', original_commit)
    assert run('status') == 0
    status = json.loads(capsys.readouterr().out)
    assert status['recorded']
    saved = history(db); db.rollback()
    assert run('apply') == 0
    assert json.loads(capsys.readouterr().out)['decision'] == status['decision']
    assert history(db) == saved


@pytest.mark.parametrize('body', ['{"command":{},"command":{}}', '{"access_token":"secret"}', '[]'])
def test_cli_rejects_duplicate_keys_or_credentials_in_request(cli, tmp_path, body):
    path = tmp_path/'command.json'; path.write_text(body)
    with pytest.raises(ValueError): cli._document(path)


def test_execution_audit_alone_blocks_0113_downgrade(db, world):
    import runpy
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from test_inventory_control_authority import PATH
    now = service.authority._now(db)
    service.append_audit_event(db, stream_key='authorization', actor_user_id=world.actor.user_id,
        action='inventory_control.configuration.execute', aggregate_type='inventory_control_authority_decision',
        aggregate_id=str(uuid4()), request_id='control-configuration:' + str(uuid4()), before_jsonb={}, after_jsonb={},
        occurred_at=now, created_at=now)
    db.commit(); before = history(db)
    with Operations.context(MigrationContext.configure(db.connection())):
        with pytest.raises(RuntimeError, match='authority must be retained'):
            runpy.run_path(str(PATH))['downgrade']()
    assert history(db) == before


def test_duplicate_execution_receipts_fail_closed_without_writes(db, login):
    reviewed = preview(db, login); result = applied(db, login, reviewed)
    event = db.get(AuditEvent, UUID(result['execution_audit_event_id']))
    now = service.authority._now(db)
    service.append_audit_event(db, stream_key=event.stream_key, actor_user_id=event.actor_user_id,
        action=event.action, aggregate_type=event.aggregate_type, aggregate_id=event.aggregate_id,
        request_id=event.request_id, before_jsonb={}, after_jsonb=event.after_jsonb, occurred_at=now, created_at=now)
    db.commit(); before = history(db)
    with pytest.raises(service.ControlConfigurationError, match='execution_evidence_invalid'):
        service.read_inventory_control_configuration(db, **login, review_sha256=reviewed['review_sha256'])
    assert history(db) == before


@pytest.mark.parametrize('bad', ['OAM_CONTROL_CONFIGURATION_DATABASE_URL', 'OAM_CONTROL_CONFIGURATION_DATABASE_NAME'])
def test_cli_has_no_fallback_for_missing_explicit_owner_target(cli, monkeypatch, bad):
    monkeypatch.setenv('OAM_CONTROL_CONFIGURATION_DATABASE_URL', 'postgresql+psycopg://star_oam_migrator@localhost/synthetic')
    monkeypatch.setenv('OAM_CONTROL_CONFIGURATION_DATABASE_NAME', 'synthetic')
    monkeypatch.delenv(bad)
    with pytest.raises(Exception): cli._connection_config()


def test_cli_argument_error_does_not_echo_accidental_token(cli, capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(['--command-file', 'unused.json', '--access-token', 'synthetic-private-token'])
    assert error.value.code == 2
    assert 'synthetic-private-token' not in capsys.readouterr().err


@pytest.mark.parametrize('change', ['head', 'current_user', 'session_user', 'database', 'version'])
def test_cli_database_preflight_refuses_other_targets_or_roles(cli, change):
    values = {'SELECT current_user':'star_oam_migrator', 'SELECT session_user':'star_oam_migrator',
              'SELECT current_database()':'synthetic', 'SHOW server_version_num':'160000'}
    if change == 'current_user': values['SELECT current_user'] = 'star_oam_api'
    if change == 'session_user': values['SELECT session_user'] = 'postgres'
    if change == 'database': values['SELECT current_database()'] = 'other'
    if change == 'version': values['SHOW server_version_num'] = '170000'
    class FakeConnection:
        def scalar(self, query): return values[str(query)]
        def scalars(self, query): return ['20261022_0112' if change == 'head' else cli.REQUIRED_HEAD]
        def execute(self, query): raise AssertionError('must fail before configuring transaction')
    with pytest.raises(ValueError): cli._database_preflight(FakeConnection(), 'synthetic')


def test_cli_head_pin_tracks_alembic_graph(cli):
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    config = Config(); config.set_main_option('script_location', str(Path(__file__).parents[1]/'alembic'))
    assert ScriptDirectory.from_config(config).get_heads() == [cli.REQUIRED_HEAD]


def test_fresh_session_can_recover_original_receipt_without_rewriting_old_session_evidence(db, world, login):
    reviewed = preview(db, login); original = applied(db, login, reviewed)
    old_session = db.scalar(select(AuthSession)); old_id = old_session.id
    now = service.authority._now(db); old_session.revoked_at = now
    new_session = AuthSession(user_id=world.actor.user_id, refresh_token_hash=uuid4().hex+uuid4().hex,
        client_type='web', device_id=uuid4().hex, ip_address='hmac:1:'+'b'*64,
        created_at=now-timedelta(seconds=1), expires_at=now+timedelta(hours=1))
    db.add(new_session); db.commit()
    login = dict(login, access_token=create_access_token(world.actor.user_id, new_session.id))
    before = history(db)
    assert service.read_inventory_control_configuration(db, **login, review_sha256=reviewed['review_sha256']) == original
    assert applied(db, login, reviewed) == original and history(db) == before
    assert db.get(AuditEvent, UUID(original['execution_audit_event_id'])).after_jsonb['auth_session_id'] == old_id


def test_expired_permission_during_final_audit_rolls_back(db, world, login, monkeypatch):
    from app.foundation_models import RoleAssignment
    now = service.authority._now(db)
    expires = now+timedelta(seconds=30)
    db.get(RoleAssignment, world.assignment).valid_to = expires
    db.commit()
    reviewed = preview(db, login); before = history(db)
    original = service.append_audit_event
    def append(*a, **kw):
        result = original(*a, **kw)
        monkeypatch.setattr(service.authority, '_now', lambda db: expires)
        return result
    monkeypatch.setattr(service, 'append_audit_event', append)
    with pytest.raises(service.authority.ControlAuthorityError, match='operator_forbidden'):
        applied(db, login, reviewed)
    db.commit(); assert history(db) == before


def test_same_coordinates_with_different_content_do_not_claim_recorded(db, login):
    reviewed = preview(db, login); applied(db, login, reviewed); before = history(db)
    changed = dict(login, command=login['command'].model_copy(update={'reason':'Changed after recording'}))
    with pytest.raises(service.ControlConfigurationError, match='request_conflict'):
        service.read_inventory_control_configuration(db, **changed, review_sha256=reviewed['review_sha256'])
    assert history(db) == before


def test_protected_pg16_helper_import_and_parent_wiring():
    from pg16_inventory_control_configuration_gate import assert_inventory_control_configuration_gate
    assert callable(assert_inventory_control_configuration_gate)
    parent = (Path(__file__).parent/'pg16_inventory_control_authority_gate.py').read_text()
    assert 'assert_inventory_control_configuration_gate(owner_engine, world)' in parent
