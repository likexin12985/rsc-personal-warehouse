#!/usr/bin/env python3
"""Dedicated loss-file migration and API-role checks on a new owned PG16."""
import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback

from run_local_pg16_stock_loss_sources_checks import manifest

CLOUD = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin',required=True)
    args=parser.parse_args(argv)
    sys.path[:0]=[str(CLOUD/'backend'),str(CLOUD/'backend/tests')]
    from local_pg16_cluster import native_cluster
    from sqlalchemy import text
    sources=manifest()
    directory=None
    try:
        with native_cluster(postgres_bin=args.postgres_bin,
                artifact_root=CLOUD/'artifacts/local-stock-loss-evidence-pg16/checks') as (directory,engines):
            (directory/'source-manifest.json').write_text(json.dumps(sources,indent=2)+'\n')
            url=engines['star_oam_migrator'].url.render_as_string(hide_password=False)
            environment=dict(os.environ,OAM_ENVIRONMENT='production',OAM_DATABASE_URL=url,
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
            def command(label,argv,expected_failure=None):
                path=directory/(label+'.log')
                with path.open('wb') as log:
                    result=subprocess.run(argv,cwd=CLOUD,env=environment,stdout=log,stderr=subprocess.STDOUT,timeout=600)
                if expected_failure:
                    assert result.returncode!=0 and expected_failure in path.read_text(),label
                else:
                    assert result.returncode==0,label
            def migrate(label,action,revision,expected_failure=None):
                command(label,[sys.executable,'-m','alembic','-c','alembic.ini',action,revision],expected_failure)
            migrate('initial-upgrade','upgrade','head')
            migrate('empty-downgrade','downgrade','20261121_0142')
            with engines['star_oam_migrator'].connect() as db:
                assert db.scalar(text('SELECT version_num FROM alembic_version'))=='20261121_0142'
                assert db.scalar(text("SELECT to_regprocedure('public.rsc_assert_stock_loss_file_authority_0143(text,bigint)')")) is None
            migrate('empty-reupgrade','upgrade','head')
            command('edge-provision',[str(Path(args.postgres_bin).resolve()/'psql'),'-X','-w','--set=ON_ERROR_STOP=1',
                '--dbname',url.replace('postgresql+psycopg:','postgresql:',1),'-v','edge_role=edge_inbox',
                '-f',str(CLOUD/'deployment/create_oam_edge_staging.sql')])
            runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
            from app.database_security import validate_production_database_security
            def security():
                validate_production_database_security(engines['star_oam_api'],
                    expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
            security()
            from pg16_stock_loss_evidence_gate import run
            result=run(engines,migrate)
            security()
            result.update(emptyRoundtrip=True,runtimeSecurityBeforeAndAfter=True,
                          sourceDrift=sorted(set(sources.items())^set(manifest().items())))
            assert result['sourceDrift']==[]
            (directory/'checks.json').write_text(json.dumps(result,indent=2)+'\n')
        state=json.loads((directory/'cluster-state.json').read_text())
        assert {k:state[k] for k in ('status','checks','serverExitCode')}==dict(status='stopped',checks='passed',serverExitCode=0)
        print(json.dumps(dict(directory=str(directory.relative_to(CLOUD)),**result)),flush=True)
        return 0
    except BaseException as error:
        detail=dict(status='failed',errorType=type(error).__name__,directory=str(directory.relative_to(CLOUD)) if directory else None,
            frames=[dict(file=frame.filename,line=frame.lineno,function=frame.name) for frame in traceback.extract_tb(error.__traceback__)])
        if directory is not None:(directory/'failure.json').write_text(json.dumps(detail,indent=2)+'\n')
        print(json.dumps(detail),flush=True)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
