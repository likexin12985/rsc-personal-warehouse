#!/usr/bin/env python3
"""Prove historical retention guards after 0149 returns in a new owned PG16.

Real daily and import facts must exist before testing their own downgrade
boundaries. Only connection coordinates are rebound to this disposable server.
"""
import argparse
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
    sys.path[:0] = [str(CLOUD / 'backend'), str(CLOUD / 'backend/tests')]
    runpy.run_path(str(CLOUD / 'backend/tests/conftest.py'))
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from local_pg16_cluster import native_cluster
    from app.database_security import validate_production_database_security
    from work_order_fixtures import canonical_source
    from pg16_stock_return_account_admission_gate import assert_return_account_admission_gate, snapshot
    import test_postgresql16_release_gate as gate

    sources = manifest(); directory = None
    try:
        with native_cluster(postgres_bin=args.postgres_bin,
                artifact_root=CLOUD / 'artifacts/local-return-account-retention-pg16/checks') as (directory, engines):
            print(json.dumps(dict(event='owned_cluster_started', directory=str(directory.relative_to(CLOUD)))), flush=True)
            (directory / 'source-manifest.json').write_text(json.dumps(sources, indent=2) + '\n')
            owner = engines['star_oam_migrator']; api = engines['star_oam_api']
            url = owner.url.render_as_string(hide_password=False)
            env = dict(os.environ, OAM_ENVIRONMENT='production', OAM_DATABASE_URL=url,
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',
                OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')

            def migrate(label, verb, destination, error=None):
                path = directory / (label + '.log')
                with path.open('wb') as output:
                    result = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'alembic.ini', verb, destination],
                        cwd=CLOUD, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=600)
                if error:
                    assert result.returncode != 0 and error in path.read_text()
                else:
                    assert result.returncode == 0, label

            def hashes():
                with owner.connect() as db:
                    return dict(db.execute(text("""SELECT proname, encode(sha256(convert_to(prosrc,'UTF8')),'hex')
                        FROM pg_proc WHERE oid IN ('public.rsc_require_opening_observation_account_0023()'::regprocedure,
                        'public.rsc_oam_runtime_binding_ready_0044()'::regprocedure)""")).all())

            def security():
                validate_production_database_security(api, expected_runtime_role='star_oam_api',
                    expected_migration_role='star_oam_migrator')

            migrate('upgrade-parent', 'upgrade', '20261127_0148'); old_hashes = hashes()
            migrate('upgrade-0149', 'upgrade', 'head'); new_hashes = hashes()
            assert old_hashes != new_hashes
            migrate('empty-downgrade', 'downgrade', '20261127_0148'); assert hashes() == old_hashes
            migrate('empty-reupgrade', 'upgrade', 'head'); assert hashes() == new_hashes
            with (directory / 'edge-provision.log').open('wb') as output:
                subprocess.run([str(Path(args.postgres_bin).resolve() / 'psql'), '-X', '-w', '--set=ON_ERROR_STOP=1',
                    '--dbname', url.replace('postgresql+psycopg:', 'postgresql:', 1), '-v', 'edge_role=edge_inbox',
                    '-f', str(CLOUD / 'deployment/create_oam_edge_staging.sql')], cwd=CLOUD, env=env,
                    stdout=output, stderr=subprocess.STDOUT, timeout=120, check=True)
            security()
            with Session(owner) as db:
                canonical_source(db); db.commit()

            def owned_url(*, role, password, database_name='rsc_pg16_release_gate'):
                assert database_name == 'rsc_pg16_release_gate' and role in engines
                return engines[role].url

            with patch.object(gate, '_sqlalchemy_url', owned_url), patch.object(gate, '_role_password', lambda _: ''):
                results = assert_return_account_admission_gate(api, owner)

            # Bind only transport coordinates to this newly owned cluster.
            # Real migrations, guards and daily service entrypoints stay intact.
            from sqlalchemy import create_engine
            from sqlalchemy.pool import NullPool
            from pg16_daily_review_runtime import run_with_capture_roles
            socket = json.loads((directory / 'cluster-state.json').read_text())['socketDirectory']
            def owned_parameters(*, role, password):
                assert role in engines
                return dict(host=socket, dbname='rsc_pg16_release_gate', user=role)
            administrator = create_engine(owner.url.set(username='postgres'), poolclass=NullPool)
            try:
                with patch.object(gate, '_sqlalchemy_url', owned_url), \
                        patch.object(gate, '_role_password', lambda _: ''), \
                        patch.object(gate, '_connection_parameters', owned_parameters), \
                        patch.object(gate, '_migration_environment', lambda **_: env):
                    def retain(destination, blocker):
                        gate._assert_retention_downgrade(destination, blocking_revision={
                            '20261108_0129':'20261109_0130',
                            '20261110_0131':'20261111_0132',
                            '20261111_0132':'20261112_0133',
                            '20261112_0133':'20261113_0134',
                        }[destination], blocker=blocker)
                    daily = run_with_capture_roles(engines, administrator,
                        lambda _: (_ for _ in ()).throw(AssertionError('callback expected')),
                        assert_retention=retain)
                    assert daily['status']=='passed' and len(daily['cases'])==12
                    from pg16_opening_fixture_gate import run as run_opening_fixture
                    imported = run_opening_fixture(engines, establish_dynamic_peer=True,
                        require_empty_inventory=False, owned_process_checks=True)
                    assert imported['status'] == 'passed'
                    gate._assert_retention_downgrade('20261119_0140',
                        blocking_revision='20261120_0141',
                        blocker='0141 existing import jobs require retention and explicit migration')
                results.append(dict(dailyRetention=daily, independentImportRetention=True))
            finally:
                administrator.dispose()
            before = snapshot(api)
            migrate('retained-downgrade', 'downgrade', '20261127_0148',
                error='0149 return inbound account admission history requires retention')
            assert snapshot(api) == before and hashes() == new_hashes
            security()
            with owner.connect() as db:
                assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261206_0157'
            assert sources == manifest()
            report = dict(migrationHead='20261206_0157', actualMigrationIntegration=True,
                emptyRoundtrip=True, populatedDowngradeRetainsFactsAndCatalog=True,
                hashesBefore=old_hashes, hashesAfter=new_hashes, checks=results,
                runtimeSecurityBeforeAndAfter=True, sourceDrift=[], productionAcceptance=False, githubReleaseGate=False)
            (directory / 'checks.json').write_text(json.dumps(report, indent=2) + '\n')
        state = json.loads((directory / 'cluster-state.json').read_text())
        assert state['status'] == 'stopped' and state['checks'] == 'passed' and state['serverExitCode'] == 0
        print(json.dumps(dict(directory=str(directory.relative_to(CLOUD)), **report)), flush=True)
        return 0
    except BaseException as error:
        detail = dict(status='failed', errorType=type(error).__name__, message=str(getattr(error, 'orig', error))[:1200],
            directory=str(directory.relative_to(CLOUD)) if directory else None,
            frames=[dict(file=f.filename, line=f.lineno, function=f.name) for f in traceback.extract_tb(error.__traceback__)])
        if directory:
            (directory / 'failure.json').write_text(json.dumps(detail, indent=2) + '\n')
        print(json.dumps(detail), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
