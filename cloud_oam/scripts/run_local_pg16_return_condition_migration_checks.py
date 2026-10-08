"""Owned PostgreSQL16 formal condition migration and runtime admission gate.

Creates a new private local cluster; no existing database URL is accepted.
Exercises real Alembic, API catalog admission, empty downgrade/reupgrade and
optional capture roles. Retained business histories have a separate gate.
"""
from hashlib import sha256
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback


def run_checks(cloud, postgres_bin):
    import pytest
    from sqlalchemy import text, create_engine
    import local_pg16_cluster
    from local_pg16_cluster import native_cluster
    from app import database_security as security
    from run_local_pg16_scrap_business_checks import manifest
    owner_cloud=Path(local_pg16_cluster.__file__).resolve().parents[2]
    source=manifest()
    # Bind every explicitly supplied source as well as the imported base.
    for directory in (cloud/'backend', cloud/'scripts'):
        for path in sorted(directory.rglob('*')):
            if path.is_file() and path.suffix in ('.py','.sql','.json') and '__pycache__' not in path.parts:
                source[str(path.resolve())]=sha256(path.read_bytes()).hexdigest()
    source[str((cloud/'alembic.ini').resolve())]=sha256((cloud/'alembic.ini').read_bytes()).hexdigest()
    source[str(Path(__file__).resolve())]=sha256(Path(__file__).read_bytes()).hexdigest()
    with native_cluster(postgres_bin=postgres_bin, artifact_root=owner_cloud/'artifacts/local-return-condition-migration-pg16') as (directory, engines):
        (directory/'source-manifest.json').write_text(json.dumps(source,indent=2)+'\n')
        print(json.dumps({'event':'owned_cluster_started','directory':str(directory)}),flush=True)
        owner = engines['star_oam_migrator']
        env = dict(os.environ, OAM_ENVIRONMENT='production',
                   OAM_DATABASE_URL=owner.url.render_as_string(hide_password=False),
                   OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',
                   OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
        def migrate(action, revision, label):
            with (directory/(label+'.log')).open('wb') as log:
                result = subprocess.run([sys.executable, '-m', 'alembic', '-c', str(cloud/'alembic.ini'), action, revision], env=env, cwd=cloud, stdout=log,
                     stderr=subprocess.STDOUT, timeout=900)
            assert result.returncode == 0, str(directory/(label+'.log'))
        def validate():
            security.validate_production_database_security(engines['star_oam_api'],
                expected_runtime_role='star_oam_api', expected_migration_role='star_oam_migrator')
        migrate('upgrade', 'head', 'upgrade')
        with owner.connect() as db:
            assert db.scalar(text('SELECT version_num FROM public.alembic_version')) == '20261228_0179'
        validate()
        print('0168 real Alembic full upgrade and API startup PASS', flush=True)
        # All mutations below are committed only inside this newly owned test
        # cluster so the separate API connection observes real catalog drift.
        mutations = (
            ('GRANT DELETE ON public.stock_condition_cases TO star_oam_api',
             'REVOKE DELETE ON public.stock_condition_cases FROM star_oam_api'),
            ('ALTER TABLE public.stock_condition_events DISABLE TRIGGER condition_immutable',
             'ALTER TABLE public.stock_condition_events ENABLE TRIGGER condition_immutable'),
        )
        for change, restore in mutations:
            with owner.begin() as db:
                db.execute(text(change))
            try:
                with pytest.raises(security.DatabaseSecurityBoundaryError):
                    validate()
            finally:
                with owner.begin() as db:
                    db.execute(text(restore))
            validate()
        print('0168 API rejects extra privilege and disabled condition trigger PASS', flush=True)
        migrate('downgrade', '20261215_0166', 'downgrade')
        with owner.connect() as db:
            assert db.scalar(text('SELECT version_num FROM public.alembic_version')) == '20261215_0166'
        migrate('upgrade', 'head', 'reupgrade')
        validate()
        # Provision optional read-only capture roles on this owned cluster only.
        # This must continue to work after the exact 0168 head transition, and
        # the API must accept only the independently validated SELECT grants.
        from app.daily_reconciliation.capture_provisioning import provision_capture_roles, require_head
        from app.daily_reconciliation.capture_security import CaptureRoleSecurityError
        from app.daily_reconciliation.capture_role_contract import ROLES
        from app.daily_reconciliation import mapping_entry
        assert mapping_entry.REQUIRED_HEAD == '20261228_0179'
        for rejected_head in ('20261215_0166', '20261216_0167', '20261220_0171'):
            with owner.connect() as db:
                tx=db.begin()
                db.execute(text('UPDATE public.alembic_version SET version_num=:head'), {'head': rejected_head})
                with pytest.raises(CaptureRoleSecurityError, match='schema_head_mismatch'):
                    require_head(db)
                tx.rollback()
                require_head(db)
        bootstrap=create_engine(owner.url.set(username='postgres'))
        try:
            with bootstrap.begin() as db:
                result=provision_capture_roles(db, database=owner.url.database,
                    passwords={role: 'local-0168-only-' + role + '-test-password' for role in ROLES}, apply=True)
                assert result['head']=='20261228_0179' and result['configured'] and result['changed']
            validate()
            with bootstrap.begin() as db:
                result=provision_capture_roles(db, database=owner.url.database, apply=True)
                assert result['configured'] and not result['changed']
        finally:
            bootstrap.dispose()
        print('0168 capture role provisioning and API full catalog PASS', flush=True)
        assert all(sha256(Path(p).read_bytes()).hexdigest() == digest for p,digest in source.items())
        (directory/'checks.json').write_text(json.dumps(dict(passed=True, source=source,
             realAlembicFullUpgrade=True, exactHead='20261228_0179', emptyDowngradeReupgrade=True,
             apiStartupCatalogVerified=True, optionalCaptureRolesVerified=True, oldCaptureHeadRejected=True, applicationMetadataRegistered=True, runtimeDriftRejections=2,
             businessLifecycleVerified=False,
             productionAcceptance=False), indent=2)+'\n')
    state=json.loads((directory/'cluster-state.json').read_text())
    assert (state['status'],state['checks'],state['serverExitCode']) == ('stopped','passed',0)
    print(json.dumps({'passed':True,'directory':str(directory),'sourceFiles':len(source)}),flush=True)
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cloud',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--postgres-bin',required=True,type=Path)
    args=parser.parse_args(argv)
    cloud=args.cloud.resolve(strict=True)
    sys.path[:0]=[str(cloud/'backend'),str(cloud/'backend/tests'),str(cloud/'scripts')]
    import conftest  # Explicit local-only settings before application imports.
    try:
        return run_checks(cloud,args.postgres_bin)
    except BaseException as error:
        print(json.dumps(dict(passed=False,errorType=type(error).__name__,message=str(error)[:500],
            frames=[dict(file=f.filename,line=f.lineno,function=f.name)
                    for f in traceback.extract_tb(error.__traceback__)])),flush=True)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
