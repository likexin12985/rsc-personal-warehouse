"""Adversarial migration checks in a freshly owned local PostgreSQL 16 cluster.

The source manifest binds this diagnostic and the migration under test.
Never accepts an existing database, DSN, or server.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cloud', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--postgres-bin', required=True)
    args = parser.parse_args()
    cloud = Path(args.cloud).resolve(strict=True)
    sys.path[:0] = [str(cloud/'backend'), str(cloud/'backend/tests'), str(cloud/'scripts')]
    from local_pg16_cluster import native_cluster
    from run_local_pg16_stock_loss_sources_checks import manifest
    from sqlalchemy import text
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.runtime.environment import EnvironmentContext
    from alembic.operations import Operations
    migration = runpy.run_path(str(cloud/'backend/alembic/versions/20261215_0166_scrap_authentication_fence.py'))
    frozen = manifest()
    assert len(frozen) > 2000
    config = Config(str(cloud/'alembic.ini'))
    config.set_main_option('script_location', str(cloud/'backend/alembic'))
    scripts = ScriptDirectory.from_config(config)
    catalog = migration['_catalog']()
    fence, ready = catalog['patches']
    signature = 'public.' + fence['before']['signature']
    table = catalog['triggers'][0]['table_name']
    trigger = catalog['triggers'][0]['name']
    directory = None
    outcomes = []

    def fingerprint(db):
        # Metadata only; no auth credentials or business payloads are logged.
        functions = list(db.execute(text("""SELECT n.nspname,p.proname,
            pg_get_functiondef(p.oid),p.proacl::text,pg_get_userbyid(p.proowner)
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE p.proname IN ('rsc_fence_scrap_seals_0165','rsc_oam_runtime_binding_ready_0044')
            ORDER BY n.nspname,p.proname,p.oid""")))
        triggers = list(db.execute(text("""SELECT n.nspname,c.relname,t.tgname,
            pg_get_triggerdef(t.oid),t.tgenabled FROM pg_trigger t
            JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE t.tgfoid=to_regprocedure(:signature) ORDER BY n.nspname,c.relname,t.tgname"""),
            {'signature': signature}))
        revisions = list(db.scalars(text('SELECT version_num FROM public.alembic_version ORDER BY version_num')))
        return hashlib.sha256(repr((functions, triggers, revisions)).encode()).hexdigest()

    def invoke(db, up):
        # Bind the unchanged real migration to Alembic's online context.
        with EnvironmentContext(config, scripts) as environment:
            environment.configure(connection=db)
            with Operations.context(environment.get_context()):
                migration['upgrade' if up else 'downgrade']()

    try:
        with native_cluster(postgres_bin=args.postgres_bin,
                artifact_root=cloud/'artifacts/local-scrap-auth-migration-admission') as (directory, engines):
            (directory/'source-manifest.json').write_text(json.dumps(frozen,indent=2)+'\n')
            (directory/'diagnostic-source.py').write_bytes(Path(__file__).read_bytes())
            print(json.dumps({'event':'owned_cluster_started','directory':str(directory)}),flush=True)
            owner = engines['star_oam_migrator']
            env = dict(os.environ,OAM_ENVIRONMENT='production',
                OAM_DATABASE_URL=owner.url.render_as_string(hide_password=False),
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',
                OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')

            def migrate(label, action, revision):
                with (directory/(label+'.log')).open('wb') as log:
                    subprocess.run([sys.executable,'-m','alembic','-c','alembic.ini',action,revision],
                        cwd=cloud,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
                print(label+' PASS',flush=True)

            migrate('initial', 'upgrade','head')
            cli = runpy.run_path(str(cloud/'scripts/configure_inventory_control.py'))
            from migration_script_cache import cache_migration_compilation
            with cache_migration_compilation(cloud/'backend/alembic/versions'):
                assert scripts.get_heads()==[cli['REQUIRED_HEAD']]
            with owner.connect() as db:
                cli['_database_preflight'](db,'rsc_pg16_release_gate')
                db.rollback()
            for invalid_head in ('20261213_0164','20261214_0165','20261216_0167'):
                with owner.connect() as db:
                    db.execute(text('UPDATE public.alembic_version SET version_num=:version'),{'version':invalid_head})
                    try:
                        cli['_database_preflight'](db,'rsc_pg16_release_gate')
                    except ValueError:
                        outcomes.append(dict(direction='cli',case=invalid_head,rejected=True,noPartialMigration=True))
                    else:
                        raise AssertionError('CLI accepted unsupported migration head')
                    finally:
                        db.rollback()
            with engines['star_oam_api'].connect() as db:
                try:
                    cli['_database_preflight'](db,'rsc_pg16_release_gate')
                except ValueError:
                    outcomes.append(dict(direction='cli',case='runtime_role',rejected=True,noPartialMigration=True))
                else:
                    raise AssertionError('CLI accepted runtime database role')
            print('cli_live_head_and_role_admission PASS',flush=True)
            with owner.connect() as db:
                head_fingerprint = fingerprint(db)
                assert list(db.scalars(text('SELECT version_num FROM alembic_version'))) == [migration['revision']]
            for up in (False, True):
                side = 'before' if up else 'after'
                cases = {
                    'fence_body': [fence[side]['definition'].replace(fence[side]['prosrc'],fence[side]['prosrc']+'\n')],
                    'readiness_body': [ready[side]['definition'].replace(ready[side]['prosrc'],ready[side]['prosrc']+'\n')],
                    'private_function_acl': ['GRANT EXECUTE ON FUNCTION '+signature+' TO star_oam_api'],
                    'public_function_acl': ['GRANT EXECUTE ON FUNCTION '+signature+' TO PUBLIC'],
                    'search_path': ['ALTER FUNCTION '+signature+' SET search_path TO public'],
                    'overload': ['CREATE FUNCTION public.rsc_fence_scrap_seals_0165(integer) RETURNS integer LANGUAGE sql AS $$ SELECT $1 $$'],
                    'disabled_trigger': [f'ALTER TABLE public.{table} DISABLE TRIGGER {trigger}'],
                    'missing_trigger': [f'DROP TRIGGER {trigger} ON public.{table}'],
                    'extra_public_trigger': ['CREATE TABLE public.auth_fence_probe(id integer)',
                        'CREATE CONSTRAINT TRIGGER auth_fence_probe AFTER INSERT ON public.auth_fence_probe DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION '+signature],
                    'extra_foreign_trigger': ['CREATE SCHEMA auth_fence_probe',
                        'CREATE TABLE auth_fence_probe.evidence(id integer)',
                        'CREATE CONSTRAINT TRIGGER auth_fence_probe AFTER INSERT ON auth_fence_probe.evidence DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION '+signature],
                    'wrong_revision': ["UPDATE public.alembic_version SET version_num='20261213_0164'"],
                }
                with owner.connect() as db:
                    clean = fingerprint(db)
                for name, statements in cases.items():
                    with owner.connect() as db:
                        transaction = db.begin()
                        try:
                            for statement in statements:
                                db.execute(text(statement))
                            damaged = fingerprint(db)
                            rejected = False
                            try:
                                invoke(db, up)
                            except ValueError as error:
                                rejected = True
                                reason = str(error)
                            else:
                                reason = 'migration accepted invalid predecessor'
                            unchanged = fingerprint(db) == damaged
                            result = dict(direction='upgrade' if up else 'downgrade',case=name,
                                rejected=rejected,noPartialMigration=unchanged,reason=reason)
                            outcomes.append(result)
                            print(json.dumps(result),flush=True)
                        finally:
                            transaction.rollback()
                    with owner.connect() as db:
                        assert fingerprint(db)==clean, 'diagnostic rollback changed baseline'
                with owner.connect().execution_options(isolation_level='REPEATABLE READ') as db:
                    try:
                        invoke(db,up)
                    except ValueError as error:
                        assert 'direct non-superuser PG16 migrator required' in str(error)
                        outcomes.append(dict(direction='upgrade' if up else 'downgrade',
                            case='repeatable_read_migrator',rejected=True,noPartialMigration=True))
                    else:
                        raise AssertionError('repeatable read migration accepted')
                if not up:
                    migrate('clean-downgrade','downgrade',migration['down_revision'])
            migrate('clean-reupgrade','upgrade',migration['revision'])
            with owner.connect() as db:
                assert fingerprint(db)==head_fingerprint, 'catalog roundtrip changed metadata'
            assert manifest()==frozen, 'candidate source drift'
            result = dict(passed=all(row['rejected'] and row['noPartialMigration'] for row in outcomes),
                cases=outcomes,exactCatalogRoundtrip=True,sourceDrift=[],productionAcceptance=False)
            (directory/'checks.json').write_text(json.dumps(result,indent=2)+'\n')
            assert result['passed'], 'migration admission failures; inspect checks.json'
        state=json.loads((directory/'cluster-state.json').read_text())
        assert (state['status'],state['checks'],state['serverExitCode'])==('stopped','passed',0)
        print(json.dumps({'passed':True,'cases':len(outcomes),'directory':str(directory)}),flush=True)
        return 0
    except BaseException as error:
        failure=dict(passed=False,errorType=type(error).__name__,message=str(error)[:500],
            directory=str(directory),frames=[dict(file=f.filename,line=f.lineno,function=f.name)
                for f in traceback.extract_tb(error.__traceback__)])
        if directory is not None:
            (directory/'failure.json').write_text(json.dumps(failure,indent=2)+'\n')
        print(json.dumps(failure),flush=True)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
