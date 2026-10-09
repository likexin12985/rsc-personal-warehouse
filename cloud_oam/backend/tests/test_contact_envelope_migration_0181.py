"""0181 structural transitions and exact admission; real PG16 is separate."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / 'alembic/versions/20261230_0181_material_request_contact_v2.py'


@pytest.fixture
def migration(monkeypatch):
    spec=importlib.util.spec_from_file_location('contact_0181',PATH)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    monkeypatch.setattr(result.context,'is_offline_mode',lambda:False)
    return result


def v1():
    return dict(schema='rsc.material_request_contact.v1',provider='aliyun_kms',kms_key_id='synthetic-old-key',
        key_version=1,ciphertext_b64='AAECAwQFBgcICQoLDA0ODxA=',nonce_b64='AAECAwQFBgcICQoL',
        aad_sha256='a'*64,mobile_hmac='hmac:1:'+'b'*64,contact_hmac='hmac:1:'+'c'*64)


def structural_db(migration, *, side='before', current=None, history=None):
    """Minimal history/catalog fixture, deliberately not a business writer."""
    engine=sa.create_engine('sqlite+pysqlite:///:memory:')
    with engine.begin() as db:
        db.exec_driver_sql('CREATE TABLE alembic_version (version_num TEXT NOT NULL)')
        db.exec_driver_sql('INSERT INTO alembic_version VALUES (?)',
            (migration.down_revision if side=='before' else migration.revision,))
        db.exec_driver_sql('CREATE TABLE material_requests (id TEXT, requester_person_id TEXT, contact_snapshot_jsonb TEXT)')
        db.exec_driver_sql('CREATE TABLE material_request_revisions (request_id TEXT, contact_snapshot_jsonb TEXT)')
        db.exec_driver_sql('CREATE TABLE openbao_data_key_pins (purpose TEXT,environment TEXT,provider_instance_id TEXT,key_path TEXT,application_key_version INTEGER,transit_key_version INTEGER,ciphertext_sha256 TEXT,created_at TEXT)')
        db.exec_driver_sql('CREATE TABLE application_key_version_claims (purpose TEXT,application_key_version INTEGER,provider TEXT,ciphertext_sha256 TEXT,created_at TEXT)')
        if current is not None:
            db.exec_driver_sql('INSERT INTO material_requests VALUES (?,?,?)',('request','person',json.dumps(current)))
        if history is not None:
            db.exec_driver_sql('INSERT INTO material_request_revisions VALUES (?,?)',('request',json.dumps(history)))
        # The four actual historical definitions are retained. These minimal
        # tables support migration/history checks, not execution of business
        # row triggers; their real INSERT/UPDATE proof belongs to PG16.
        for row in migration.DATA['sqlite_triggers'].values():db.exec_driver_sql(row[side])
    return engine


def trigger_state(engine):
    with engine.connect() as db:
        return dict(db.exec_driver_sql("SELECT name,sql FROM sqlite_master WHERE type='trigger'").all())


def apply(engine,migration,action):
    with engine.begin() as db:
        with Operations.context(MigrationContext.configure(db)):
            getattr(migration,action)()
        db.exec_driver_sql('UPDATE alembic_version SET version_num=?',
            (migration.revision if action=='upgrade' else migration.down_revision,))


def test_empty_and_retained_v1_history_roundtrip_preserve_full_predecessor_guards(migration):
    for current,history in ((None,None),(v1(),v1())):
        engine=structural_db(migration,current=current,history=history)
        try:
            before=trigger_state(engine)
            apply(engine,migration,'upgrade')
            assert trigger_state(engine)=={name:row['after'] for name,row in migration.DATA['sqlite_triggers'].items()}
            apply(engine,migration,'downgrade')
            assert trigger_state(engine)==before
            with engine.connect() as db:
                rows=db.exec_driver_sql('SELECT contact_snapshot_jsonb FROM material_requests').scalars().all()
                assert rows==([] if current is None else [json.dumps(current)])
        finally:engine.dispose()


@pytest.mark.parametrize('name',(
    'trg_material_requests_insert_guard_0029','trg_material_requests_update_guard_0029',
    'trg_material_request_revisions_insert_guard_0029','trg_material_request_revisions_update_guard_0029'))
def test_each_sqlite_predecessor_guard_drift_is_rejected_before_any_replacement(migration,name):
    engine=structural_db(migration)
    try:
        with engine.begin() as db:
            db.exec_driver_sql('DROP TRIGGER '+name)
            db.exec_driver_sql(migration.DATA['sqlite_triggers'][name]['before'].replace('BEFORE','BEFORE /* drift */',1))
        before=trigger_state(engine)
        with pytest.raises(ValueError,match='exact SQLite predecessor guard'):
            apply(engine,migration,'upgrade')
        assert trigger_state(engine)==before
    finally:engine.dispose()


@pytest.mark.parametrize('location',('current','history'))
def test_any_v2_current_or_old_revision_blocks_downgrade_without_dropping_guards(migration,location):
    value=v1()|{'schema':'rsc.material_request_contact.v2'}
    engine=structural_db(migration,side='after',current=value if location=='current' else v1(),
                         history=value if location=='history' else v1())
    try:
        before=trigger_state(engine)
        with pytest.raises(ValueError,match='non-v1 contact history'):
            apply(engine,migration,'downgrade')
        assert trigger_state(engine)==before
    finally:engine.dispose()


def test_orphan_or_malformed_legacy_history_cannot_cross_migration(migration):
    for current,history in ((None,v1()),(v1(),v1()|{'unexpected':'field'})):
        engine=structural_db(migration,current=current,history=history)
        try:
            before=trigger_state(engine)
            with pytest.raises(ValueError,match='historical contact envelope invalid'):
                apply(engine,migration,'upgrade')
            assert trigger_state(engine)==before
        finally:engine.dispose()


def test_sqlite_wrong_head_is_rejected_before_trigger_changes(migration):
    engine=structural_db(migration)
    try:
        with engine.begin() as db:db.exec_driver_sql("UPDATE alembic_version SET version_num='20261228_0179'")
        before=trigger_state(engine)
        with pytest.raises(ValueError,match='exact SQLite predecessor'):
            apply(engine,migration,'upgrade')
        assert trigger_state(engine)==before
    finally:engine.dispose()


def test_migration_changes_only_contact_fragments_and_readiness_without_new_grants(migration):
    from hashlib import sha256
    data=migration.DATA
    assert data['previousRevision']=='20261229_0180'
    assert set(data['functions'])=={'rsc_guard_material_request_identity_0029()',
        'rsc_guard_material_request_revision_0029()','rsc_oam_runtime_binding_ready_0044()'}
    for row in data['functions'].values():
        for side in ('before','after'):assert sha256(row[side].encode()).hexdigest()==row[side+'Sha256']
    for name,before in data['catalog']['functions']['before'].items():
        after=data['catalog']['functions']['after'][name]
        assert {k:v for k,v in before.items() if k not in ('prosrc','definition')}=={
            k:v for k,v in after.items() if k not in ('prosrc','definition')}
    request=data['functions']['rsc_guard_material_request_identity_0029()']['after']
    assert '0168 shipment projection does not match handover command' in request
    assert 'NEW.reservation_status' in request
    assert 'GRANT ' not in PATH.read_text() and 'SECURITY DEFINER' not in PATH.read_text()


def test_runtime_overlay_and_guard_registration_require_complete_predecessors(migration):
    from app import material_request_contact_envelope_security as security
    data=security.DATA
    before=dict(revision=data['previousRevision'],after=deepcopy(data['readiness']['before']),
                afterSha256=data['readiness']['beforeSha256'])
    previous=SimpleNamespace(DATA=deepcopy(before))
    security.overlay(previous)
    assert previous.DATA['revision']==migration.revision
    assert before['after']==data['readiness']['before']
    for field in ('prosrc','owner','acl','proconfig','prosecdef'):
        value=SimpleNamespace(DATA=deepcopy(before));value.DATA['after'][field]='drift'
        expected=deepcopy(value.DATA)
        with pytest.raises(ValueError,match='exact readiness predecessor'):security.overlay(value)
        assert value.DATA==expected
    hashes={(name[:-2],''):row['beforeSha256'] for name,row in data['functions'].items()}
    namespace={'MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256':deepcopy(hashes)}
    security.register(namespace)
    assert namespace['MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256']=={
        (name[:-2],''):row['afterSha256'] for name,row in data['functions'].items()}
    invalid={'MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256':deepcopy(hashes)}
    invalid['MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256'][('rsc_guard_material_request_revision_0029','')]='drift'
    expected=deepcopy(invalid)
    with pytest.raises(ValueError,match='exact contact guard predecessor'):security.register(invalid)
    assert invalid==expected
