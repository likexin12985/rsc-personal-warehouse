"""Actual API-role negative admission, audit atomicity and two-way races."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
from pathlib import Path
import runpy
import time
from uuid import uuid4
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.formal_access import load_formal_principal
from app.foundation_models import OpeningImportCommandSeal as Seal, FileObject
from app.formal_services import formal_files
from app.formal_services.opening_count_import_seals import ImportSealCommand, seal_opening_import, read_opening_import_seal, _coordinates, _context
from app.formal_services.opening_count_import_intake import create_opening_count_import_job
from app.formal_services.opening_count_import_jobs import OpeningCountImportJobError
from formal_file_integrity import FileUploadIntentInput, _prepare_upload

SECRET='synthetic-import-seal-hmac-key-32-bytes'
MIME='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


def exercise_import_seals(api,*,policy_engine,actor,manager_id,command):
    with Session(api) as db: manager=load_formal_principal(db,manager_id)
    cases=[]
    def blocked(application):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            with api.connect() as probe:
                if probe.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name=:app AND wait_event_type='Lock')"),{'app':application}): return
            time.sleep(.02)
        raise AssertionError('concurrent admission never reached database lock wait')
    def new():
        return ImportSealCommand(task_id=command.task_id,round_id=command.round_id,scope_id=command.scope_id,
            actor_person_id=actor.person_id,authorization_version=actor.authorization_version,
            source_sha256='a'*64,size_bytes=1024,upload_key=uuid4().hex,import_key=uuid4().hex)
    def file(db,cmd,available=False):
        user,file_id,key_hash,_=_coordinates(db,cmd,SECRET)
        prepared=_prepare_upload(FileUploadIntentInput(purpose='opening_count_import',original_filename='seal-fixture.xlsx',
            sha256=cmd.source_sha256,size_bytes=cmd.size_bytes,mime_type=MIME),maximum_size_bytes=8388608)
        storage_key=formal_files._storage_key('opening_count_import',file_id)
        row=FileObject(id=file_id,storage_key=storage_key,sha256=cmd.source_sha256,size_bytes=cmd.size_bytes,
            mime_type=MIME,original_filename='seal-fixture.xlsx',uploaded_by=user,status='pending',
            created_at=db.scalar(text('SELECT transaction_timestamp()')),
            metadata_jsonb=formal_files._pending_metadata(file_id=file_id,storage_key=storage_key,prepared=prepared,actor=actor,
                key_hash=key_hash,request_hash=formal_files._upload_request_hash(prepared),provider_code='aliyun_oss_v2'))
        db.add(row);db.flush()
        if available:
            row.status='available';row.metadata_jsonb={**row.metadata_jsonb,'completion':{'etag_sha256':'b'*64,
                'head_manifest_sha256':'c'*64,'verified_at':db.scalar(text('SELECT transaction_timestamp()')).isoformat()}}
            db.flush()
        return row
    def seal(db,cmd): return seal_opening_import(db,actor=manager,command=cmd,idempotency_hmac_secret=SECRET)
    def read(db,cmd): return read_opening_import_seal(db,actor=manager,command=cmd,idempotency_hmac_secret=SECRET)
    def intake(db,cmd,source_id):
        return create_opening_count_import_job(db,actor=actor,task_id=cmd.task_id,round_id=cmd.round_id,
            scope_id=cmd.scope_id,source_file_id=source_id,idempotency_key=cmd.import_key,request_id='seal-gate-intake')
    def rejected(action,code=None):
        with Session(api) as db:
            try: action(db);db.commit()
            except (OpeningCountImportJobError,formal_files.FormalFileError) as exc:
                if code: assert exc.code==code,(exc.code,code)
                db.rollback()
            except DBAPIError as exc:
                assert exc.orig.sqlstate in ('23514','42501'),exc.orig.sqlstate
                db.rollback()
            else: raise AssertionError('sealed operation unexpectedly admitted')

    for phase in ('absent','pending','available'):
        cmd=new()
        if phase!='absent':
            with Session(api) as db: file(db,cmd,phase=='available');db.commit()
        with Session(api) as db:
            proof=seal(db,cmd);db.commit()
            assert proof['permanent_nonexecution'] and proof['source_object_may_exist'] and not proof['automatic_retry_allowed']
        with Session(api) as db:
            assert read(db,cmd)==proof
            db.rollback()
            assert seal(db,cmd)==proof
            db.commit()
        rejected(lambda db:intake(db,cmd,proof['source_file_id']),'opening_import_permanently_sealed')
        if phase=='absent': rejected(lambda db:file(db,cmd))
        else:
            def late_complete(db):
                row=db.get(FileObject,proof['source_file_id']);row.metadata_jsonb=dict(row.metadata_jsonb)
                # Force a raw UPDATE even if no content changes.
                db.execute(text('UPDATE files SET status=status WHERE id=:id'),{'id':row.id})
            rejected(late_complete)
        rejected(lambda db:seal(db,replace(cmd,size_bytes=2048)))
        cases.append('seal-'+phase+'-source-read-replay-late-source-and-intake-refused')

    # Exercise the actual downgrade body before this fixture admits any jobs.
    # A later/populated fixture instead hits the existing job-retention branch.
    # Always roll back the isolated migration transaction, including on failure.
    def retained():
        with policy_engine.connect() as connection:
            return tuple(connection.scalar(text(sql)) for sql in (
                'SELECT version_num FROM alembic_version',
                'SELECT jsonb_agg(to_jsonb(s) ORDER BY id) FROM opening_import_command_seals s',
                "SELECT jsonb_agg(to_jsonb(a) ORDER BY id) FROM audit_events a WHERE aggregate_type='opening_import_command_seal'",
                'SELECT jsonb_agg(to_jsonb(f) ORDER BY id) FROM files f',
                'SELECT jsonb_agg(to_jsonb(j) ORDER BY id) FROM file_jobs j',
            ))
    before=retained()
    assert before[1] and before[2]
    with policy_engine.connect() as connection:
        has_jobs=connection.scalar(text("SELECT EXISTS(SELECT 1 FROM file_jobs WHERE job_type='import')"))
        connection.rollback()
        try:
            migration=runpy.run_path(str(Path(__file__).resolve().parents[1]/'alembic/versions/20261120_0141_opening_count_import_jobs.py'))
            with Operations.context(MigrationContext.configure(connection)):
                migration['downgrade']()
        except (DBAPIError, RuntimeError) as exc:
            expected=('0141 existing import jobs require retention and explicit migration' if has_jobs
                      else '0141 original import seals must be retained')
            assert expected in str(exc),str(exc)
        else:
            raise AssertionError('downgrade discarded retained import evidence')
        finally:
            connection.rollback()
    assert retained()==before
    from app.database_security import validate_production_database_security
    validate_production_database_security(api,expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    cases.append('nonempty-import-downgrade-preserves-seals-audit-files-catalog' if has_jobs
                 else 'seal-only-downgrade-refused-preserves-seals-audit-files-catalog')

    absent=new()
    rejected(lambda db:read(db,absent),'opening_import_seal_not_found')
    cases.append('missing-seal-read-grants-no-retry-or-write')
    with Session(api) as db:
        current,grant,expected,_=_context(db,manager,absent,SECRET)
        values=dict(id=uuid4(),**expected,reviewer_user_id=current.user_id,reviewer_person_id=current.person_id,
            reviewer_authorization_version=current.authorization_version,reviewer_assignment_id=grant.assignment_id)
        db.rollback()
    def unaudited(db):
        db.add(Seal(**values,created_at=db.scalar(text('SELECT clock_timestamp()'))));db.flush()
    rejected(unaudited)
    rejected(lambda db:seal_opening_import(db,actor=replace(manager,authorization_version=manager.authorization_version+1),
        command=absent,idempotency_hmac_secret=SECRET))
    cases.extend(('seal-without-same-transaction-audit-rejected','seal-stale-management-authority-refused'))
    rejected(lambda db:db.execute(text('UPDATE opening_import_command_seals SET size_bytes=size_bytes')))
    rejected(lambda db:db.execute(text('DELETE FROM opening_import_command_seals')))
    rejected(lambda db:db.execute(text('TRUNCATE opening_import_command_seals')))
    cases.append('seal-api-cannot-update-delete-truncate')

    # A seal wins the file+request lock: a concurrently sent intake must then fail.
    cmd=new()
    with Session(api) as db: source=file(db,cmd,True);source_id=source.id;db.commit()
    def later_intake():
        with Session(api) as db:
            db.execute(text("SET LOCAL application_name='import-seal-later-intake'"))
            try: intake(db,cmd,source_id);db.commit()
            except OpeningCountImportJobError as exc: db.rollback();return exc.code
            raise AssertionError('intake crossed committed seal')
    with ThreadPoolExecutor(max_workers=1) as pool, Session(api) as first:
        seal(first,cmd)
        future=pool.submit(later_intake)
        blocked("import-seal-later-intake")
        try: future.result(timeout=.25)
        except TimeoutError: pass
        else: raise AssertionError('concurrent intake did not wait for seal')
        first.commit()
        assert future.result(timeout=15)=='opening_import_permanently_sealed'
    cases.append('concurrent-seal-wins-no-job-admission')

    # An admitted job wins: even a still-queued import cannot become a negative fact.
    cmd=new()
    with Session(api) as db: source=file(db,cmd,True);source_id=source.id;db.commit()
    def later_seal():
        with Session(api) as db:
            db.execute(text("SET LOCAL application_name='import-seal-later-seal'"))
            try: seal(db,cmd);db.commit()
            except OpeningCountImportJobError as exc: db.rollback();return exc.code
            raise AssertionError('seal crossed accepted job')
    with ThreadPoolExecutor(max_workers=1) as pool, Session(api) as first:
        accepted_id,_=intake(first,cmd,source_id)
        future=pool.submit(later_seal)
        blocked("import-seal-later-seal")
        try: future.result(timeout=.25)
        except TimeoutError: pass
        else: raise AssertionError('concurrent seal did not wait for admission')
        first.commit()
        assert future.result(timeout=15)=='opening_import_already_accepted'
    cases.append('concurrent-intake-wins-no-negative-fact')
    with Session(api) as db:
        assert db.scalar(select(Seal.id).where(Seal.source_file_id==source_id)) is None
    from app.formal_services.opening_count_import_termination import cancel_opening_count_import
    with Session(api) as db:
        cancel_opening_count_import(db,actor=actor,job_id=accepted_id,idempotency_key=cmd.import_key,request_id='seal-fixture-clean-cancel')
        db.commit()
    # HTTP commit succeeded but acknowledgement failed. Only exact readback
    # resolves it; the read endpoint performs no INSERT/UPDATE/DELETE/COMMIT.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from sqlalchemy import event
    from app.routers import formal_opening_imports as http
    from app.dependencies import get_formal_principal
    from app.config import get_settings
    cmd=new(); app=FastAPI();app.include_router(http.router,prefix='/api')
    class LostAck(Session):
        def commit(self):
            super().commit()
            raise RuntimeError('synthetic lost COMMIT acknowledgement')
    factory={'value':lambda:LostAck(api)}
    app.dependency_overrides[get_formal_principal]=lambda:manager
    app.dependency_overrides[get_settings]=lambda:SimpleNamespace(opening_count_import_enabled=True,
        database_url='postgresql+psycopg://synthetic',file_storage_configuration_ready=lambda:True,file_idempotency_hmac_secret=SECRET)
    app.dependency_overrides[http.get_formal_file_storage_adapter]=lambda:SimpleNamespace(provider_code='aliyun_oss_v2')
    app.dependency_overrides[http.get_import_session_factory]=lambda:factory['value']
    client=TestClient(app);base='/api/v1/stocktakes/opening/imports/opening-count/command-seals'
    body={k:(str(v) if hasattr(v,'hex') else v) for k,v in vars(cmd).items() if k not in {'upload_key','import_key','source_file_id'}}
    headers={'Idempotency-Key':cmd.import_key,'X-Original-Upload-Key':cmd.upload_key}
    lost=client.post(base,json=body,headers=headers)
    assert lost.status_code==503,lost.text
    factory['value']=lambda:Session(api)
    statements=[]
    def capture(_conn,_cursor,statement,*_):statements.append(statement.lstrip().split(None,1)[0].upper())
    event.listen(api,'before_cursor_execute',capture)
    try: recovered=client.get(base+'/recovery',params=body,headers=headers)
    finally:event.remove(api,'before_cursor_execute',capture)
    assert recovered.status_code==200,recovered.text
    assert recovered.json()['permanent_nonexecution'] and recovered.json()['source_object_may_exist']
    assert not set(statements)&{'INSERT','UPDATE','DELETE','COMMIT'},statements
    assert 'no-store' in recovered.headers['cache-control']
    assert not set(recovered.json())&{'storage_key','upload_key','import_key','url','original_filename'}
    cases.append('seal-http-commit-ack-lost-original-read-only-recovery')
    cases.extend(_exercise_seal_authority(api,policy_engine=policy_engine,actor=actor,
        manager=manager,new_command=new,retained=retained))
    return cases


def _exercise_seal_authority(api,*,policy_engine,actor,manager,new_command,retained):
    """Persisted grant changes exercise both services and raw database guards."""
    cases=[]
    def invoke(db,principal,cmd,read_only=False):
        operation=read_opening_import_seal if read_only else seal_opening_import
        return operation(db,actor=principal,command=cmd,idempotency_hmac_secret=SECRET)
    def raw_values(principal,cmd):
        with Session(api) as db:
            current,grant,expected,_=_context(db,principal,cmd,SECRET)
            return dict(id=uuid4(),**expected,reviewer_user_id=current.user_id,
                reviewer_person_id=current.person_id,reviewer_authorization_version=current.authorization_version,
                reviewer_assignment_id=grant.assignment_id)
    def reject_service(bind,principal,cmd,read_only=False):
        with Session(bind,join_transaction_mode='create_savepoint') as db:
            try: invoke(db,principal,cmd,read_only)
            except OpeningCountImportJobError as exc:
                assert exc.http_status_code==403,(exc.code,exc.http_status_code)
                db.rollback()
            else: raise AssertionError('unauthorized seal service access accepted')
    def reject_raw(bind,principal,values):
        with Session(bind,join_transaction_mode='create_savepoint') as db:
            try:
                db.add(Seal(**{**values,'reviewer_authorization_version':principal.authorization_version},
                    created_at=db.scalar(text('SELECT clock_timestamp()'))))
                # Must fail at BEFORE INSERT on authority, not the later
                # missing-audit check; this is an independent raw SQL boundary.
                db.flush()
            except DBAPIError as exc:
                assert exc.orig.sqlstate in ('23514','42501'),exc.orig.sqlstate
                assert '0141 seal coordinates invalid' in str(exc.orig) or '0126' in str(exc.orig),str(exc.orig)
                db.rollback()
            else: raise AssertionError('raw seal insert bypassed current authority')

    # A real regional manager can seal inside their region; a different current
    # manager reads the same immutable original and audit evidence.
    regional=new_command()
    with Session(api) as db:
        proof=invoke(db,actor,regional);db.commit()
    with Session(api) as db:
        reread=invoke(db,manager,regional,True)
        assert reread['seal_id']==proof['seal_id'] and reread['terminal_audit_id']==proof['terminal_audit_id']
        assert reread['reviewer_person_id']==manager.person_id
    cases.append('seal-current-regional-authority-and-independent-manager-read')

    for kind in ('outside-region','technician'):
        cmd=new_command();values=raw_values(actor,cmd);before=retained()
        with api.connect() as connection:
            transaction=connection.begin()
            try:
                if kind=='outside-region':
                    region=connection.scalar(text("SELECT id::text FROM organizations WHERE org_type='region_company' AND status='active' AND id<>(SELECT region_org_id FROM stocktake_tasks WHERE id=:task) ORDER BY id LIMIT 1"),{'task':cmd.task_id})
                    assert region
                    connection.execute(text('UPDATE role_assignments SET scope_id=:region WHERE user_id=:user'),{'region':region,'user':actor.user_id})
                else:
                    connection.execute(text("UPDATE role_assignments SET role_id=(SELECT id FROM roles WHERE code='technician'),scope_type='person',scope_id=:person WHERE user_id=:user"),{'person':str(actor.person_id),'user':actor.user_id})
                with Session(connection,join_transaction_mode='rollback_only') as db:
                    changed=load_formal_principal(db,actor.user_id)
                reject_service(connection,changed,cmd)
                reject_service(connection,changed,regional,True)
                reject_raw(connection,changed,values)
            finally: transaction.rollback()
        assert retained()==before
        cases.append('seal-actual-'+kind+'-service-read-and-raw-insert-refused')

    for action in ('read','manage'):
        cmd=new_command();values=raw_values(manager,cmd);before=retained()
        with policy_engine.begin() as connection:
            policy=connection.execute(text("SELECT id,effect FROM role_permissions WHERE role_id IN (SELECT role_id FROM role_assignments WHERE user_id=:user) AND permission_id IN (SELECT id FROM permissions WHERE resource='stocktake' AND action=:action) FOR UPDATE"),{'user':manager.user_id,'action':action}).all()
            assert policy
            for identifier,_ in policy: connection.execute(text("UPDATE role_permissions SET effect='deny' WHERE id=:id"),{'id':identifier})
        try:
            with Session(api) as db:
                changed=load_formal_principal(db,manager.user_id)
                assert not changed.allows(db,'stocktake',action)
            reject_service(api,changed,cmd)
            reject_service(api,changed,regional,True)
            reject_raw(api,changed,values)
        finally:
            with policy_engine.begin() as connection:
                for identifier,effect in policy:
                    connection.execute(text('UPDATE role_permissions SET effect=:effect WHERE id=:id'),{'id':identifier,'effect':effect})
        assert retained()==before
        cases.append('seal-explicit-'+action+'-deny-service-read-and-raw-insert-refused')

    cmd=new_command();before=retained()
    with api.connect() as connection:
        transaction=connection.begin()
        try:
            connection.execute(text('UPDATE users SET authorization_version=authorization_version+1 WHERE id=:id'),{'id':actor.user_id})
            with Session(connection,join_transaction_mode='rollback_only') as db:
                proof=invoke(db,manager,cmd);db.commit()
            connection.exec_driver_sql('SET CONSTRAINTS ALL IMMEDIATE')
            with Session(connection,join_transaction_mode='rollback_only') as db:
                reread=invoke(db,manager,cmd,True)
                assert reread['authorization_version']==actor.authorization_version
                assert reread['seal_id']==proof['seal_id']
        finally: transaction.rollback()
    assert retained()==before
    cases.append('seal-revoked-original-version-preserved-with-current-manager-authority')
    return cases
