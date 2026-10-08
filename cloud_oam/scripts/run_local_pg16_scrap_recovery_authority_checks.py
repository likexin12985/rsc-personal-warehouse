#!/usr/bin/env python3
"""Verify recovery authority on a fresh full 0164 PostgreSQL 16 catalog.

Only a native binary directory is accepted, never an existing host or DSN.
"""
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(CLOUD/'backend'), str(CLOUD/'backend/tests')]
    from local_pg16_cluster import native_cluster
    directory = None
    snapshot = manifest()
    try:
        with native_cluster(postgres_bin=args.postgres_bin,
                artifact_root=CLOUD/'artifacts/local-scrap-recovery-authority-pg16') as (directory, engines):
            (directory/'source-manifest.json').write_text(json.dumps(snapshot,indent=2)+'\n')
            url = engines['star_oam_migrator'].url.render_as_string(hide_password=False)
            environment = dict(os.environ, OAM_ENVIRONMENT='production', OAM_DATABASE_URL=url,
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',
                OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
            for label, command in (
                ('migration',[sys.executable,'-m','alembic','-c','alembic.ini','upgrade','head']),
                ('edge-provision',[str(Path(args.postgres_bin).resolve()/'psql'),'-X','--dbname',
                    url.replace('postgresql+psycopg:','postgresql:',1),'-v','edge_role=edge_inbox',
                    '-f',str(CLOUD/'deployment/create_oam_edge_staging.sql')]),
            ):
                with (directory/(label+'.log')).open('wb') as log:
                    subprocess.run(command,cwd=CLOUD,env=environment,stdout=log,
                        stderr=subprocess.STDOUT,check=True,timeout=600)
            runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
            from pg16_scrap_recovery_authority_gate import run
            result = run(engines)
            result['sourceDrift'] = sorted(set(snapshot.items()) ^ set(manifest().items()))
            assert result['sourceDrift']==[]
            (directory/'checks.json').write_text(json.dumps(result,indent=2)+'\n')
        state = json.loads((directory/'cluster-state.json').read_text())
        assert {k:state[k] for k in ('status','checks','serverExitCode')}==dict(
            status='stopped',checks='passed',serverExitCode=0)
        print(json.dumps(dict(directory=str(directory.relative_to(CLOUD)),**result)),flush=True)
        return 0
    except BaseException as error:
        detail = dict(status='failed',errorType=type(error).__name__,
            evidenceDirectory=str(directory.relative_to(CLOUD)) if directory else None,
            frames=[dict(file=Path(f.filename).name,line=f.lineno,function=f.name)
                for f in traceback.extract_tb(error.__traceback__)])
        if directory is not None:
            (directory/'failure.json').write_text(json.dumps(detail,indent=2)+'\n')
        print(json.dumps(detail),flush=True)
        traceback.print_exc()
        return 1


if __name__=='__main__':
    raise SystemExit(main())
