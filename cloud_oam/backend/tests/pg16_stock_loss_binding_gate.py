"""0144 attachment contract fixtures, separate from full loss admission.

The full migrated database keeps formal loss permissions unseeded. A second fresh database
uses minimal parent/file tables and the exact installed functions to exercise
positive and adversarial attachment COMMITs. It is not stock-posting evidence.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def installed_contract(engines, migration):
    """Inspect the real migrated catalog and try runtime SQL bypasses."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from pathlib import Path
    from migration_script_cache import cache_migration_compilation
    with cache_migration_compilation(Path(__file__).parents[1] / 'alembic/versions'):
        head = ScriptDirectory.from_config(Config('alembic.ini')).get_current_head()
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == head
        definitions = {}
        for signature in ('rsc_canonical_reconciliation_json_0026(jsonb)',
                          'rsc_guard_work_order_facts_0090()', migration['FUNCTION']+'()'):
            definitions[signature] = db.scalar(text('SELECT pg_get_functiondef(to_regprocedure(:name))'),
                dict(name='public.'+signature))
        assert db.scalar(text("SELECT encode(sha256(convert_to(prosrc,'UTF8')),'hex') FROM pg_proc WHERE oid=to_regprocedure(:name)"),
            dict(name='public.'+migration['FUNCTION']+'()')) == migration['BODY_HASH']
        assert db.scalar(text("SELECT to_regprocedure('public.rsc_check_loss_submission_0145(uuid)')")) is not None
        assert db.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgname IN ('trg_stock_operation_orders_loss_admission_0142','trg_stock_operation_lines_loss_admission_0142')")) == 0
        assert not db.scalar(text("SELECT EXISTS(SELECT 1 FROM permissions WHERE resource='stock_operation' AND action='submit_loss')"))
    for statement, reason in (
        ('ALTER TABLE stock_loss_files DISABLE TRIGGER ALL', 'owner'),
        ('TRUNCATE stock_loss_files', 'permission denied'),
        ('SELECT rsc_guard_stock_loss_file_binding_0144()', 'permission denied'),
    ):
        with api.connect() as db:
            with pytest.raises(DBAPIError, match=reason):
                db.exec_driver_sql(statement)
            db.rollback()
    with api.connect() as db:
        for action in ('SELECT', 'INSERT'):
            assert db.scalar(text('SELECT has_table_privilege(current_user,:table,:action)'), dict(table='stock_loss_files',action=action))
        for action in ('UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER'):
            assert not db.scalar(text('SELECT has_table_privilege(current_user,:table,:action)'), dict(table='stock_loss_files',action=action))
    with api.connect() as db, Operations.context(MigrationContext.configure(db)):
        with pytest.raises(DBAPIError, match='direct schema owner required'):
            migration['downgrade']()
        db.rollback()
    with owner.connect() as db, Operations.context(MigrationContext.configure(db)):
        # Inject source drift in a rollback-only owner transaction. The migration
        # must reject it before removing either table or proof function.
        db.exec_driver_sql('''CREATE OR REPLACE FUNCTION public.rsc_guard_stock_loss_file_binding_0144()
            RETURNS trigger LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,public
            AS $b$ BEGIN RETURN NULL; END; $b$''')
        with pytest.raises(DBAPIError, match='source ownership or ACL drift'):
            migration['downgrade']()
        db.rollback()
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == head
        assert db.scalar(text("SELECT encode(sha256(convert_to(prosrc,'UTF8')),'hex') FROM pg_proc WHERE oid='public.rsc_guard_stock_loss_file_binding_0144()'::regprocedure")) == migration['BODY_HASH']
    return definitions


def fixture_checks(engines, migration, definitions):
    owner, api = engines['star_oam_migrator'], engines['star_oam_api']
    with owner.begin() as db, Operations.context(MigrationContext.configure(db)):
        assert not db.scalar(text("SELECT to_regclass('public.stock_operation_orders')"))
        db.exec_driver_sql('CREATE TABLE alembic_version(version_num text)')
        db.execute(text('INSERT INTO alembic_version VALUES (:head)'), dict(head=migration['revision']))
        db.exec_driver_sql('CREATE TABLE inventory_ledger_heads(stream_key text PRIMARY KEY)')
        db.exec_driver_sql("INSERT INTO inventory_ledger_heads VALUES ('inventory')")
        db.exec_driver_sql('''CREATE TABLE stock_operation_orders(
            id uuid PRIMARY KEY, operation_type text, status text, actor_user_id text,
            requester_id uuid, authorization_version bigint, command_jsonb jsonb,
            plan_jsonb jsonb, created_at timestamptz, UNIQUE(id,operation_type))''')
        db.exec_driver_sql('''CREATE TABLE files(id uuid PRIMARY KEY, status text, uploaded_by text,
            metadata_jsonb jsonb, sha256 text, size_bytes bigint, mime_type text,
            original_filename text, storage_key text, created_at timestamptz)''')
        migration['_schema']()
        for sql in definitions.values():
            db.execute(text(sql))
        for name, (table, events, function, _, deferred) in migration['TRIGGERS'].items():
            sql = (f'CREATE CONSTRAINT TRIGGER {name} AFTER {events} ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW'
                if deferred else f"CREATE TRIGGER {name} BEFORE {events} ON {table} FOR EACH {'STATEMENT' if events=='TRUNCATE' else 'ROW'}")
            db.exec_driver_sql(sql+f' EXECUTE FUNCTION public.{function}()')
            db.exec_driver_sql(f'ALTER TABLE {table} ENABLE ALWAYS TRIGGER {name}')
        db.exec_driver_sql('GRANT SELECT,INSERT ON stock_loss_files TO star_oam_api')
        for signature in definitions:
            db.exec_driver_sql(f'REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,star_oam_api')
        actual = db.scalar(text("SELECT encode(sha256(convert_to(prosrc,'UTF8')),'hex') FROM pg_proc WHERE oid='public.rsc_guard_stock_loss_file_binding_0144()'::regprocedure"))
        assert actual == migration['BODY_HASH']

    def prepare(change=None):
        from types import SimpleNamespace
        from app.formal_services.formal_files import _pending_metadata
        from formal_file_integrity import _upload_request_hash
        identifier, parent, person = uuid4(), uuid4(), uuid4()
        at = datetime.now(timezone.utc)
        prepared = SimpleNamespace(purpose='stock_loss_evidence', original_filename='现场照片.jpg',
            sha256='a'*64, size_bytes=128, mime_type='image/jpeg')
        actor = SimpleNamespace(user_id='synthetic-loss-user', person_id=person, authorization_version=1)
        key = f'formal-files/v1/stock_loss_evidence/{identifier.hex[:2]}/{identifier.hex}'
        metadata = _pending_metadata(file_id=identifier, storage_key=key, prepared=prepared,
            actor=actor, key_hash='b'*64, request_hash=_upload_request_hash(prepared), provider_code='aliyun_oss_v2')
        metadata['completion'] = dict(verified_at=(at-timedelta(seconds=1)).isoformat(),
            etag_sha256='c'*64, head_manifest_sha256='d'*64)
        file = dict(id=identifier, status='available', uploaded_by=actor.user_id, metadata_jsonb=metadata,
            sha256=prepared.sha256, size_bytes=prepared.size_bytes, mime_type=prepared.mime_type,
            original_filename=prepared.original_filename, storage_key=key, created_at=at-timedelta(seconds=2))
        expected = dict(file_id=str(identifier), original_filename=file['original_filename'], sha256=file['sha256'],
            size_bytes=file['size_bytes'], mime_type=file['mime_type'], metadata_sha256=digest(metadata))
        order = dict(id=parent, operation_type='loss_report', status='submitted', actor_user_id=actor.user_id,
            requester_id=person, authorization_version=1, command_jsonb=dict(evidence_file_ids=[str(identifier)]),
            plan_jsonb=dict(evidence=[expected]), created_at=at)
        binding = dict(id=uuid4(), operation_id=parent, operation_type='loss_report', file_id=identifier,
            metadata_sha256=digest(metadata), created_at=at)
        if change:
            change(file, order, binding)
        with owner.begin() as db:
            # JSON values are explicit bound parameters; never SQL interpolation.
            for table, row in (('files',file),('stock_operation_orders',order)):
                values = {k:json.dumps(v) if isinstance(v,dict) else v for k,v in row.items()}
                params = ','.join(f'CAST(:{k} AS jsonb)' if isinstance(v,dict) else ':'+k for k,v in row.items())
                db.execute(text(f'INSERT INTO {table}({",".join(row)}) VALUES ({params})'), values)
        return binding

    def insert(db, binding):
        db.execute(text('''INSERT INTO stock_loss_files(id,operation_id,operation_type,file_id,metadata_sha256,created_at)
            VALUES (:id,:operation_id,:operation_type,:file_id,:metadata_sha256,:created_at)'''), binding)

    good = prepare()
    with api.begin() as db:
        insert(db,good)
    with owner.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM stock_loss_files')) == 1

    bad = {
        'pending': lambda f,o,b: f.update(status='pending'),
        'wrong_uploader': lambda f,o,b: f.update(uploaded_by='unrelated-user'),
        'wrong_person': lambda f,o,b: o.update(requester_id=uuid4()),
        'wrong_authorization': lambda f,o,b: o.update(authorization_version=2),
        'wrong_filename': lambda f,o,b: f.update(original_filename='替换文件.jpg'),
        'wrong_size': lambda f,o,b: f.update(size_bytes=129),
        'wrong_digest': lambda f,o,b: b.update(metadata_sha256='f'*64),
        'wrong_binding_time': lambda f,o,b: b.update(created_at=b['created_at']+timedelta(seconds=1)),
        'late_completion': lambda f,o,b: f['metadata_jsonb']['completion'].update(verified_at=(o['created_at']+timedelta(seconds=1)).isoformat()),
        'missing_manifest': lambda f,o,b: o.update(plan_jsonb=dict(evidence=[])),
        'repeated_intent': lambda f,o,b: o['command_jsonb']['evidence_file_ids'].append(str(f['id'])),
        'wrong_parent_type': lambda f,o,b: o.update(operation_type='return'),
    }
    for label, change in bad.items():
        binding = prepare(change)
        with api.connect() as db:
            if label == 'wrong_parent_type':
                with pytest.raises(DBAPIError, match='foreign key'):
                    insert(db,binding)
            else:
                insert(db,binding)  # Admission succeeds; deferred proof rejects COMMIT.
                with pytest.raises(DBAPIError, match='0144'):
                    db.commit()
            db.rollback()
        with owner.connect() as db:
            assert db.scalar(text('SELECT count(*) FROM stock_loss_files')) == 1, label
    with api.connect() as db:
        duplicate = dict(good,id=uuid4())
        with pytest.raises(DBAPIError, match='uq_stock_loss_files_file'):
            insert(db,duplicate)
        db.rollback()
    for sql in ('UPDATE stock_loss_files SET metadata_sha256=metadata_sha256',
                'DELETE FROM stock_loss_files', 'TRUNCATE stock_loss_files'):
        with owner.connect() as db:
            with pytest.raises(DBAPIError, match='append-only'):
                db.exec_driver_sql(sql)
            db.rollback()
    with owner.connect() as db, Operations.context(MigrationContext.configure(db)):
        with pytest.raises(DBAPIError, match='history requires retention'):
            migration['downgrade']()
        db.rollback()
    with owner.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM stock_loss_files')) == 1
    return dict(positiveRuntimeCommit=True, rejectedCases=list(bad), exclusiveFileBinding=True,
        immutableUpdateDeleteTruncate=True, retainedBindingBlocksDowngrade=True,
        fixtureOnly=True, stockPostingProven=False)
