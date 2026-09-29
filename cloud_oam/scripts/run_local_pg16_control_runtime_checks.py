#!/usr/bin/env python3
"""Run shared control business checks on a newly owned native PG16 cluster.

No DSN or existing data directory is accepted. Only connection coordinates are
bound to the factory-owned Unix socket; hosted guards and bootstrap are never
patched or invoked, and real migration/security/business assertions stay intact.
"""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback
from unittest.mock import patch

from run_local_pg16_stock_loss_sources_checks import manifest

CLOUD = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    args = parser.parse_args(argv)
    sys.path[:0] = [str(CLOUD/'backend'), str(CLOUD/'backend/tests'), str(CLOUD.parent)]
    runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
    from local_pg16_cluster import native_cluster
    from sqlalchemy.orm import Session
    from work_order_fixtures import canonical_source
    from pg16_gate_progress import run_gate_phase
    import test_postgresql16_release_gate as gate

    sources = manifest()
    directory = None
    try:
        with native_cluster(postgres_bin=args.postgres_bin,
                artifact_root=CLOUD/'artifacts/local-control-runtime-pg16/checks') as (directory, engines):
            print(json.dumps(dict(event='owned_cluster_started',
                                  directory=str(directory.relative_to(CLOUD)))), flush=True)
            (directory/'source-manifest.json').write_text(json.dumps(sources, indent=2)+'\n')
            owner = engines['star_oam_migrator']
            url = owner.url.render_as_string(hide_password=False)
            environment = dict(os.environ, OAM_ENVIRONMENT='production', OAM_DATABASE_URL=url,
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',
                OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
            for label, command in (
                ('upgrade', [sys.executable, '-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head']),
                ('edge-provision', [str(Path(args.postgres_bin).resolve()/'psql'), '-X', '-w',
                    '--set=ON_ERROR_STOP=1', '--dbname', url.replace('postgresql+psycopg:', 'postgresql:', 1),
                    '-v', 'edge_role=edge_inbox', '-f', str(CLOUD/'deployment/create_oam_edge_staging.sql')]),
            ):
                with (directory/(label+'.log')).open('wb') as output:
                    subprocess.run(command, cwd=CLOUD, env=environment, stdout=output,
                                   stderr=subprocess.STDOUT, check=True, timeout=600)
            with Session(owner) as db:
                canonical_source(db)
                db.commit()
            socket = json.loads((directory/'cluster-state.json').read_text())['socketDirectory']

            def owned_url(*, role, password, database_name='rsc_pg16_release_gate'):
                assert database_name == 'rsc_pg16_release_gate' and role in engines
                return engines[role].url

            def owned_parameters(*, role, password):
                assert role in engines
                return dict(host=socket, dbname='rsc_pg16_release_gate', user=role)

            def migration_environment(*, database_name='rsc_pg16_release_gate'):
                assert database_name == 'rsc_pg16_release_gate'
                return environment

            with ExitStack() as bindings:
                for name, value in (
                    ('_sqlalchemy_url', owned_url), ('_role_password', lambda _: ''),
                    ('_connection_parameters', owned_parameters),
                    ('_migration_environment', migration_environment),
                    ('_admin_sqlalchemy_url', lambda: owner.url.set(username='postgres')),
                ):
                    bindings.enter_context(patch.object(gate, name, value))
                assert gate._current_revision() == gate.HEAD_REVISION
                gate._validate_runtime_security(engines['star_oam_api'])
                run_gate_phase('control_business_checks', lambda: gate._run_control_business_checks(
                    engines['star_oam_api'], engines['star_oam_projector'], engines['edge_inbox']))
                assert gate._current_revision() == gate.HEAD_REVISION
            drift = sorted(set(sources.items()) ^ set(manifest().items()))
            assert not drift, 'source changed during native control checks'
            result = dict(status='passed', migrationHead=gate.HEAD_REVISION,
                sharedControlBusinessChecks=True, predecessorHistoryPreserved=True,
                sourceDrift=[], githubReleaseGate=False, productionAcceptance=False)
            (directory/'checks.json').write_text(json.dumps(result, indent=2)+'\n')
        state = json.loads((directory/'cluster-state.json').read_text())
        assert state['status'] == 'stopped' and state['checks'] == 'passed' and state['serverExitCode'] == 0
        print(json.dumps(dict(result, directory=str(directory.relative_to(CLOUD)))), flush=True)
        return 0
    except BaseException as error:
        failure = dict(status='failed', errorType=type(error).__name__,
            directory=str(directory.relative_to(CLOUD)) if directory else None,
            frames=[dict(file=f.filename, line=f.lineno, function=f.name)
                    for f in traceback.extract_tb(error.__traceback__)])
        if directory is not None:
            (directory/'failure.json').write_text(json.dumps(failure, indent=2)+'\n')
        print(json.dumps(failure), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
