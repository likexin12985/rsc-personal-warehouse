"""Reproduce overlapping opening/notification retention on fresh owned PG16."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback
from unittest.mock import patch

CLOUD=next(p for p in Path(__file__).resolve().parents if (p/'alembic.ini').is_file() and (p/'backend/tests').is_dir())
sys.path[:0]=[str(CLOUD/'scripts'),str(CLOUD/'backend'),str(CLOUD/'backend/tests'),str(CLOUD.parent)]
from run_local_pg16_stock_loss_sources_checks import manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin',required=True)
    args=parser.parse_args()
    runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
    from local_pg16_cluster import native_cluster
    from sqlalchemy import text
    from app.database_security import validate_production_database_security
    import test_postgresql16_release_gate as gate
    source=manifest(); directory=None
    try:
        with native_cluster(postgres_bin=args.postgres_bin,artifact_root=CLOUD/'artifacts/local-notification-retention-pg16/checks') as (directory,engines):
            print(json.dumps(dict(event='owned_cluster_started',directory=str(directory.relative_to(CLOUD)))),flush=True)
            (directory/'source-manifest.json').write_text(json.dumps(source,indent=2)+'\n')
            (directory/'probe-sha256.txt').write_text(hashlib.sha256(Path(__file__).read_bytes()).hexdigest()+'\n')
            owner,api=engines['star_oam_migrator'],engines['star_oam_api']
            url=owner.url.render_as_string(hide_password=False)
            environment=dict(os.environ,OAM_ENVIRONMENT='production',OAM_DATABASE_URL=url,
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
            for label,command in (
                ('upgrade',[sys.executable,'-m','alembic','-c','alembic.ini','upgrade','head']),
                ('edge-provision',[str(Path(args.postgres_bin).resolve()/'psql'),'-X','-w','--set=ON_ERROR_STOP=1','--dbname',
                    url.replace('postgresql+psycopg:','postgresql:',1),'-v','edge_role=edge_inbox','-f',str(CLOUD/'deployment/create_oam_edge_staging.sql')]),
            ):
                with (directory/(label+'.log')).open('wb') as log:
                    subprocess.run(command,cwd=CLOUD,env=environment,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
            def security():
                validate_production_database_security(api,expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
            security()
            # Build only the real opening facts that precede notification
            # retention in the shared CI gate. A loss-source fixture would add
            # unrelated 0143 files and mask the older boundary under test.
            from sqlalchemy import select
            from sqlalchemy.orm import Session
            from app.foundation_models import Role
            from app.stocktake_models import FormalStocktakeTask
            from test_formal_access import make_organization,make_user,assign
            from pg16_opening_publication_fixture import prepare_stocktake_inventory
            with Session(owner) as db:
                hq=make_organization(db,name='Synthetic retention HQ')
                region=make_organization(db,name='Synthetic retention region',parent=hq)
                admin,_=make_user(db,hq,name='Synthetic retention HQ reviewer')
                manager,_=make_user(db,region,name='Synthetic retention region counter')
                roles={role.code:role for role in db.scalars(select(Role))}
                assign(db,admin,roles['admin'],scope_type='national',scope_id='*')
                assign(db,manager,roles['provincial_manager'],scope_type='organization',scope_id=str(region.id))
                db.commit();admin_id,manager_id=admin.id,manager.id
            fixture=prepare_stocktake_inventory(owner,engines['edge_inbox'],actor_user_id=admin_id,assignee_user_id=manager_id)
            task_id=gate._establish_multiround_stocktake_location(api,fixture=fixture,
                actor_user_id=admin_id,assignee_user_id=manager_id)
            with Session(api) as db:
                assert db.get(FormalStocktakeTask,task_id).status=='closed'
                assert not db.scalar(text("SELECT EXISTS (SELECT 1 FROM files WHERE metadata_jsonb->>'purpose'='stock_loss_evidence')"))
            print('Real zero opening: start/count/independent HQ review/post/close PASS',flush=True)
            # Opening itself does not create audience manifests. Create one
            # actual zero-channel event through the API role for a synthetic
            # person with no account; recording is not external delivery.
            from sqlalchemy.orm import Session
            from app.foundation_models import Person
            from app.formal_services.notification_events import record_business_notification
            from uuid import uuid4
            with Session(owner) as db:
                organization=db.scalar(text('SELECT organization_id FROM people ORDER BY id LIMIT 1'))
                person=Person(id=uuid4(),organization_id=organization,employee_no='RETENTION-'+uuid4().hex,
                    name='Synthetic notification retention target',employment_status='active')
                db.add(person);db.commit();person_id=person.id
            with Session(api) as db:
                at=datetime.now(timezone.utc);identifier=uuid4()
                event=record_business_notification(db,event_type='pg16_retention',business_type='pg16_retention',
                    business_id=identifier,dedup_key='pg16-retention:'+str(identifier),payload={},
                    recipient_person_id=person_id,occurred_at=at,now=at)
                assert event.recipient_count==0
                db.commit()
            with owner.connect() as db:
                assert db.scalar(text('SELECT EXISTS (SELECT 1 FROM stocktake_tasks WHERE opening_authorization_version IS NOT NULL)'))
                assert db.scalar(text('SELECT EXISTS (SELECT 1 FROM notification_events WHERE target_manifest_sha256 IS NOT NULL)'))
                assert db.scalar(text('SELECT EXISTS (SELECT 1 FROM notification_person_targets)'))
                assert not db.scalar(text('SELECT EXISTS (SELECT 1 FROM stock_operation_return_inbounds)'))
                assert not db.scalar(text('SELECT EXISTS (SELECT 1 FROM opening_start_command_seals)'))
            def snapshot():
                # Retain only hashes, not synthetic identity or channel payloads.
                with owner.connect() as db:
                    names=tuple(db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")))
                    tables={name:db.scalar(text('SELECT md5(COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),\'[]\'::jsonb)::text) FROM public."'+name.replace('"','""')+'" t')) for name in names}
                    functions=db.scalar(text("SELECT md5(jsonb_agg(to_jsonb(r) ORDER BY r.oid)::text) FROM (SELECT p.oid,p.proname,p.prosrc,p.proacl,p.prosecdef,p.proconfig,p.provolatile FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public') r"))
                    triggers=db.scalar(text("SELECT md5(jsonb_agg(to_jsonb(t) ORDER BY t.oid)::text) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'"))
                return dict(tables=tables,functions=functions,triggers=triggers)
            before=snapshot()
            socket=json.loads((directory/'cluster-state.json').read_text())['socketDirectory']
            def parameters(*,role,password):
                assert role in engines
                return dict(host=socket,dbname='rsc_pg16_release_gate',user=role)
            def owned_url(*,role,password,database_name='rsc_pg16_release_gate'):
                assert role in engines and database_name=='rsc_pg16_release_gate'
                return engines[role].url
            with patch.object(gate,'_connection_parameters',parameters),patch.object(gate,'_sqlalchemy_url',owned_url), \
                    patch.object(gate,'_role_password',lambda _:''),patch.object(gate,'_migration_environment',lambda **_:environment), \
                    patch.object(gate,'ALEMBIC_COMMAND_TIMEOUT_SECONDS',600):
                old=gate._run_alembic('downgrade','20261018_0108',expect_success=False)
                output=old.stdout+old.stderr
                (directory/'old-assertion-reproduction.log').write_text(output)
                assert '0126 downgrade blocked: opening authorization evidence must be retained' in output
                assert '0109 downgrade blocked: notification target evidence must be retained' not in output
                assert snapshot()==before
                gate._assert_retention_downgrade('20261018_0108',blocking_revision='20261019_0109',
                    blocker='0109 downgrade blocked: notification target evidence must be retained')
            assert snapshot()==before
            security()
            assert manifest()==source
            result=dict(passed=True,migrationHead=gate.HEAD_REVISION,oldAssertionFailureReproduced=True,
                actualOpeningAndNotificationFacts=True,currentChain0126Refusal=True,independent0109Refusal=True,
                completePublicDataAndCatalogUnchanged=True,runtimeSecurityBeforeAndAfter=True,
                sourceDrift=[],localAlembicTimeoutSeconds=600,githubReleaseGate=False,productionAcceptance=False,finishedAt=datetime.now(timezone.utc).isoformat())
            (directory/'checks.json').write_text(json.dumps(result,indent=2)+'\n')
        state=json.loads((directory/'cluster-state.json').read_text())
        assert (state['status'],state['checks'],state['serverExitCode'])==('stopped','passed',0)
        print(json.dumps(dict(directory=str(directory.relative_to(CLOUD)),**result)),flush=True)
        return 0
    except BaseException as error:
        if directory is not None and isinstance(error,subprocess.TimeoutExpired):
            output=(error.stdout or b'')+(error.stderr or b'')
            (directory/'timed-out-migration.log').write_bytes(output if isinstance(output,bytes) else output.encode())
        detail=dict(status='failed',errorType=type(error).__name__,message=str(getattr(error,'orig',error))[:1200],
            directory=str(directory.relative_to(CLOUD)) if directory else None,
            frames=[dict(file=f.filename,line=f.lineno,function=f.name) for f in traceback.extract_tb(error.__traceback__)])
        if directory:(directory/'failure.json').write_text(json.dumps(detail,indent=2)+'\n')
        print(json.dumps(detail),flush=True)
        return 1


if __name__=='__main__':raise SystemExit(main())
