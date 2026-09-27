#!/usr/bin/env python3
"""Verify typed stock migration with committed returns in a fresh owned PG16."""
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
    sys.path[:0] = [str(CLOUD/'backend'), str(CLOUD/'backend/tests'), str(CLOUD.parent)]
    from local_pg16_cluster import native_cluster
    sources = manifest()
    directory = None
    try:
        with native_cluster(postgres_bin=args.postgres_bin,
                artifact_root=CLOUD/'artifacts/local-typed-stock-pg16/checks') as (directory, engines):
            (directory/'source-manifest.json').write_text(json.dumps(sources, indent=2)+'\n')
            url = engines['star_oam_migrator'].url.render_as_string(hide_password=False)
            environment = dict(os.environ, OAM_ENVIRONMENT='production', OAM_DATABASE_URL=url,
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator', OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')

            def command(label, arguments):
                with (directory/(label+'.log')).open('wb') as log:
                    subprocess.run(arguments, cwd=CLOUD, env=environment, stdout=log,
                        stderr=subprocess.STDOUT, check=True, timeout=600)

            def migrate(label, action, revision):
                command(label, [sys.executable, '-m', 'alembic', '-c', 'alembic.ini', action, revision])

            migrate('initial-upgrade', 'upgrade', 'head')
            command('edge-provision', [str(Path(args.postgres_bin).resolve()/'psql'), '-X', '-w',
                '--set=ON_ERROR_STOP=1', '--dbname', url.replace('postgresql+psycopg:', 'postgresql:', 1),
                '-v', 'edge_role=edge_inbox', '-f', str(CLOUD/'deployment/create_oam_edge_staging.sql')])
            runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
            from pg16_stock_operation_typed_context_gate import run
            result = run(engines, migrate)
            result['sourceDrift'] = sorted(set(sources.items()) ^ set(manifest().items()))
            assert result['sourceDrift'] == []
            (directory/'checks.json').write_text(json.dumps(result, indent=2)+'\n')
        state = json.loads((directory/'cluster-state.json').read_text())
        assert {k:state[k] for k in ('status','checks','serverExitCode')} == dict(
            status='stopped', checks='passed', serverExitCode=0)
        print(json.dumps(dict(directory=str(directory.relative_to(CLOUD)), **result)), flush=True)
        return 0
    except BaseException as error:
        detail = dict(status='failed', errorType=type(error).__name__,
            directory=str(directory.relative_to(CLOUD)) if directory else None,
            frames=[dict(file=frame.filename, line=frame.lineno, function=frame.name)
                    for frame in traceback.extract_tb(error.__traceback__)])
        if directory is not None:
            (directory/'failure.json').write_text(json.dumps(detail, indent=2)+'\n')
        print(json.dumps(detail), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
