#!/usr/bin/env python3
"""Verify loss-source previews on two new owned PostgreSQL 16 clusters.

Each tracking mode starts with a fresh database, publishes explicit synthetic
source evidence and establishes personal stock through the real API lifecycle.
No supplied host, DSN, existing database or production permission is accepted.
"""
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

CLOUD = Path(__file__).resolve().parents[1]


def manifest():
    root = CLOUD.parent
    paths = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=root)
    return {path: hashlib.sha256((root/path).read_bytes()).hexdigest()
            for path in sorted(set(paths.decode().split('\0')))
            if path and not path.endswith('.md') and (root/path).is_file()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    args = parser.parse_args(argv)
    sys.path[:0] = [str(CLOUD/'backend'), str(CLOUD/'backend/tests'), str(CLOUD.parent)]
    from local_pg16_cluster import native_cluster
    from sqlalchemy import text
    from test_postgresql16_release_gate import HEAD_REVISION
    snapshot = manifest()
    reports = []
    directory = None
    try:
        for tracking in ('quantity', 'serial'):
            with native_cluster(postgres_bin=args.postgres_bin,
                    artifact_root=CLOUD/'artifacts/local-stock-loss-sources-pg16/checks') as (directory, engines):
                (directory/'source-manifest.json').write_text(json.dumps(snapshot, indent=2)+'\n')
                result = dict(startedAt=datetime.now(timezone.utc).isoformat(), tracking=tracking,
                              passed=False, githubReleaseGate=False, productionAcceptance=False)
                url = engines['star_oam_migrator'].url.render_as_string(hide_password=False)
                environment = dict(os.environ, OAM_ENVIRONMENT='production', OAM_DATABASE_URL=url,
                    OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator', OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
                commands = (
                    ('migration', [sys.executable, '-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head']),
                    ('edge-provision', [str(Path(args.postgres_bin).resolve()/'psql'), '-X', '--dbname',
                        url.replace('postgresql+psycopg:', 'postgresql:', 1), '-v', 'edge_role=edge_inbox',
                        '-f', str(CLOUD/'deployment/create_oam_edge_staging.sql')]),
                )
                for label, command in commands:
                    with (directory/(label+'.log')).open('wb') as log:
                        subprocess.run(command, cwd=CLOUD, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT, check=True, timeout=600)
                with engines['star_oam_migrator'].connect() as db:
                    assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD_REVISION
                # Synthetic settings only; the helper receives explicit owned
                # engines and never opens the application's configured DB.
                runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
                from pg16_stock_loss_sources_gate import run
                result.update(run(engines, tracking=tracking))
                result['sourceDrift'] = sorted(set(snapshot.items()) ^ set(manifest().items()))
                assert result['sourceDrift'] == []
                (directory/'checks.json').write_text(json.dumps(result, indent=2)+'\n')
            state = json.loads((directory/'cluster-state.json').read_text())
            assert {k: state[k] for k in ('status', 'checks', 'serverExitCode')} == dict(
                status='stopped', checks='passed', serverExitCode=0)
            reports.append(dict(directory=str(directory.relative_to(CLOUD)), **result))
            print(json.dumps(reports[-1]), flush=True)
    except BaseException as error:
        detail = dict(status='failed', errorType=type(error).__name__,
            evidenceDirectory=str(directory) if directory else None,
            frames=[dict(file=frame.filename, line=frame.lineno, function=frame.name)
                    for frame in traceback.extract_tb(error.__traceback__)])
        if directory is not None:
            (directory/'failure.json').write_text(json.dumps(detail, indent=2)+'\n')
        print(json.dumps(detail), flush=True)
        return 1
    print(json.dumps(dict(passed=True, modes=[r['tracking'] for r in reports],
                         githubReleaseGate=False, productionAcceptance=False)), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
