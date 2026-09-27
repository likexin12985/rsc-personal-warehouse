#!/usr/bin/env python3
"""0144 full migration and isolated binding contracts on new owned PG16s."""
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    args = parser.parse_args(argv)
    sys.path[:0] = [str(CLOUD/'backend'), str(CLOUD/'backend/tests')]
    from local_pg16_cluster import native_cluster
    from migration_script_cache import cache_migration_compilation
    from sqlalchemy import text
    runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
    from pg16_stock_loss_binding_gate import installed_contract, fixture_checks
    from app.database_security import validate_production_database_security
    sources = manifest()
    with cache_migration_compilation(CLOUD/'backend/alembic/versions'):
        migration = runpy.run_path(str(CLOUD/'backend/alembic/versions/20261123_0144_stock_loss_file_bindings.py'))
    directory = None
    try:
        reports = []
        for phase in ('full_migration', 'isolated_contract'):
            with native_cluster(postgres_bin=args.postgres_bin,
                    artifact_root=CLOUD/'artifacts/local-stock-loss-binding-pg16/checks') as (directory, engines):
                (directory/'source-manifest.json').write_text(json.dumps(sources, indent=2)+'\n')
                result = dict(phase=phase, productionAcceptance=False, githubReleaseGate=False)
                if phase == 'full_migration':
                    url = engines['star_oam_migrator'].url.render_as_string(hide_password=False)
                    environment = dict(os.environ, OAM_ENVIRONMENT='production', OAM_DATABASE_URL=url,
                        OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator', OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
                    def command(label, command):
                        with (directory/(label+'.log')).open('wb') as log:
                            subprocess.run(command, cwd=CLOUD, env=environment, stdout=log,
                                stderr=subprocess.STDOUT, check=True, timeout=600)
                    for label, action, revision in (
                        ('initial-upgrade','upgrade','head'),
                        ('empty-downgrade','downgrade','20261122_0143'),
                        ('empty-reupgrade','upgrade','head'),
                    ):
                        command(label,[sys.executable,'-m','alembic','-c','alembic.ini',action,revision])
                        if action == 'downgrade':
                            with engines['star_oam_migrator'].connect() as db:
                                assert db.scalar(text('SELECT version_num FROM alembic_version')) == revision
                                assert db.scalar(text("SELECT to_regclass('public.stock_loss_files')")) is None
                    command('edge-provision',[str(Path(args.postgres_bin).resolve()/'psql'),'-X','-w','--set=ON_ERROR_STOP=1',
                        '--dbname',url.replace('postgresql+psycopg:','postgresql:',1),'-v','edge_role=edge_inbox',
                        '-f',str(CLOUD/'deployment/create_oam_edge_staging.sql')])
                    def security():
                        validate_production_database_security(engines['star_oam_api'],
                            expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
                    security()
                    definitions = installed_contract(engines, migration)
                    security()
                    with engines['star_oam_migrator'].connect() as db:
                        head = db.scalar(text('SELECT version_num FROM alembic_version'))
                    result.update(emptyRoundtrip=True, runtimeSecurityBeforeAndAfter=True,
                        lossPermissionUnseeded=True, runtimeAclDenials=True, head=head)
                else:
                    result.update(fixture_checks(engines, migration, definitions))
                result['sourceDrift'] = sorted(set(sources.items()) ^ set(manifest().items()))
                assert not result['sourceDrift']
                (directory/'checks.json').write_text(json.dumps(result,indent=2)+'\n')
            state = json.loads((directory/'cluster-state.json').read_text())
            assert {k:state[k] for k in ('status','checks','serverExitCode')} == dict(status='stopped',checks='passed',serverExitCode=0)
            reports.append(dict(directory=str(directory.relative_to(CLOUD)),**result))
            print(json.dumps(reports[-1]),flush=True)
        return 0
    except BaseException as error:
        detail = dict(status='failed', errorType=type(error).__name__,
            directory=str(directory.relative_to(CLOUD)) if directory else None,
            frames=[dict(file=frame.filename,line=frame.lineno,function=frame.name) for frame in traceback.extract_tb(error.__traceback__)])
        if directory is not None:
            (directory/'failure.json').write_text(json.dumps(detail,indent=2)+'\n')
        print(json.dumps(detail),flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
