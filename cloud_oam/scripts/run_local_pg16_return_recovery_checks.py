#!/usr/bin/env python3
"""Return request seal guards on a new owned PG16, never an existing DSN."""
import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback

from run_local_pg16_legacy_reversal_checks import manifest

CLOUD=Path(__file__).resolve().parents[1]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin',required=True)
    args=parser.parse_args(argv)
    sys.path[:0]=[str(CLOUD/'backend'),str(CLOUD/'backend/tests'),str(CLOUD.parent)]
    from local_pg16_cluster import native_cluster
    runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
    from app.database_security import validate_production_database_security
    from pg16_return_recovery_fixture import prepare
    from pg16_stock_return_recovery_gate import assert_stock_return_recovery_gate
    from sqlalchemy import text
    sources=manifest();directory=None
    try:
        with native_cluster(postgres_bin=args.postgres_bin,
                artifact_root=CLOUD/'artifacts/local-return-recovery-pg16/checks') as (directory,engines):
            (directory/'source-manifest.json').write_text(json.dumps(sources,indent=2)+'\n')
            url=engines['star_oam_migrator'].url.render_as_string(hide_password=False)
            environment=dict(os.environ,OAM_ENVIRONMENT='production',OAM_DATABASE_URL=url,
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
            with (directory/'upgrade.log').open('wb') as log:
                subprocess.run([sys.executable,'-m','alembic','-c','alembic.ini','upgrade','head'],
                    cwd=CLOUD,env=environment,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600)
            with (directory/'edge-provision.log').open('wb') as log:
                subprocess.run([str(Path(args.postgres_bin).resolve()/'psql'),'-X','-w','--set=ON_ERROR_STOP=1',
                    '--dbname',url.replace('postgresql+psycopg:','postgresql:',1),'-v','edge_role=edge_inbox',
                    '-f',str(CLOUD/'deployment/create_oam_edge_staging.sql')],cwd=CLOUD,env=environment,
                    stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
            def security():
                validate_production_database_security(engines['star_oam_api'],expected_runtime_role='star_oam_api',
                    expected_migration_role='star_oam_migrator')
            security();prepare(engines)
            result=assert_stock_return_recovery_gate(engines['star_oam_api'],engines['star_oam_migrator'])
            security()
            with engines['star_oam_migrator'].connect() as db:
                revision=db.scalar(text('SELECT version_num FROM alembic_version'))
            result=dict(proofs=result,migrationHead=revision,runtimeSecurityBeforeAndAfter=True,
                sourceDrift=sorted(set(sources.items())^set(manifest().items())),
                productionAcceptance=False,githubReleaseGate=False)
            assert result['sourceDrift']==[]
            (directory/'checks.json').write_text(json.dumps(result,indent=2)+'\n')
        state=json.loads((directory/'cluster-state.json').read_text())
        assert {k:state[k] for k in ('status','checks','serverExitCode')}==dict(status='stopped',checks='passed',serverExitCode=0)
        print(json.dumps(dict(directory=str(directory.relative_to(CLOUD)),**result)),flush=True)
        return 0
    except BaseException as error:
        detail=dict(status='failed',errorType=type(error).__name__,message=str(getattr(error,'orig',error))[:800],
            directory=str(directory.relative_to(CLOUD)) if directory else None,
            frames=[dict(file=f.filename,line=f.lineno,function=f.name) for f in traceback.extract_tb(error.__traceback__)])
        if directory is not None:(directory/'failure.json').write_text(json.dumps(detail,indent=2)+'\n')
        print(json.dumps(detail),flush=True);return 1


if __name__=='__main__':
    raise SystemExit(main())
