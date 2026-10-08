#!/usr/bin/env python3
"""Prepare a fresh, explicitly isolated socket-only PG16 candidate fixture.

This is a synthetic test entry, not a deployer. It accepts no DSN/credentials
and must run inside the reviewed disposable container pair. Host orchestration
must first verify container identities, network/mount/resource boundaries.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

import psycopg
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

SOCKET = Path('/pgsocket')
WORKSPACE = Path('/workspace')
EVIDENCE = Path('/evidence')
DATABASE = 'rsc_pg16_release_gate'
HEAD = '20261229_0180'
FUNCTIONS = ('rsc_reject_key_provider_binding_mutation_0180',
             'rsc_guard_application_key_version_claim_0180',
             'rsc_claim_application_key_version_0180')
TABLES = ('application_key_version_claims', 'kms_data_key_pins', 'openbao_data_key_pins')


def main():
    # Assertions below are evidence checks, so an optimized interpreter must
    # stop before any filesystem/database fixture work.
    if not __debug__ or os.environ.get('PYTHONOPTIMIZE'):
        raise RuntimeError('unoptimized candidate interpreter required')
    # These fixed paths are created only by the external one-shot orchestrator.
    if os.geteuid() != 70 or not WORKSPACE.is_dir() or not EVIDENCE.is_dir():
        raise RuntimeError('isolated candidate context required')
    if any(name.startswith(('PG', 'OAM_', 'ALIBABA_', 'BAO_', 'VAULT_')) for name in os.environ):
        raise RuntimeError('unconfigured candidate environment required')
    with psycopg.connect(host=str(SOCKET), dbname='postgres', user='postgres', autocommit=True) as db:
        identity = db.execute("SELECT current_setting('server_version_num')::int, current_setting('data_directory'), current_setting('listen_addresses'), current_database(), current_user").fetchone()
        if not (160000 <= identity[0] < 170000 and identity[1:] == ('/tmp/rsc-pgdata', '', 'postgres', 'postgres')):
            raise RuntimeError('fresh isolated PG16 identity required')
        if db.execute("SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname").fetchall() != [('postgres',)]:
            raise RuntimeError('candidate database is not empty')
        if db.execute("SELECT rolname FROM pg_roles WHERE rolname NOT LIKE 'pg\\_%' ESCAPE '\\' ORDER BY rolname").fetchall() != [('postgres',)]:
            raise RuntimeError('candidate roles are not empty')
    sys.path[:0] = [str(WORKSPACE/'backend'), str(WORKSPACE/'backend/tests')]
    from local_pg16_cluster import _bootstrap
    _bootstrap(SOCKET)
    url = f'postgresql+psycopg://star_oam_migrator@/{DATABASE}?host={SOCKET}'
    environment = dict(os.environ, OAM_DATABASE_URL=url, OAM_ENVIRONMENT='production',
        OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',
        OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api', OAM_MIGRATION_CACHE_EXECUTION='1')

    def migrate(label, direction, revision):
        with (EVIDENCE/(label+'.log')).open('xb') as log:
            result = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'alembic.ini', direction, revision],
                cwd=WORKSPACE, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=600)
        if result.returncode != 0:
            raise RuntimeError(label+'_failed')

    migrate('upgrade-0179', 'upgrade', '20261228_0179')
    migrator = create_engine(url, poolclass=NullPool, hide_parameters=True)
    with migrator.begin() as db:
        db.execute(text("INSERT INTO kms_data_key_pins(purpose,kms_key_id,application_key_version,kms_key_version_id,ciphertext_sha256,created_at) VALUES ('authentication_idempotency','synthetic-legacy-key',1,'synthetic-legacy-version',:digest,:created)"),
                   dict(digest='a'*64, created=datetime(2026, 10, 8, tzinfo=timezone.utc)))
    migrate('upgrade-0180', 'upgrade', HEAD)
    with migrator.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version')) == HEAD
        claim = db.execute(text('SELECT purpose,application_key_version,provider,ciphertext_sha256,created_at FROM application_key_version_claims')).one()
        assert tuple(claim) == ('authentication_idempotency', 1, 'aliyun_kms', 'a'*64, datetime(2026, 10, 8, tzinfo=timezone.utc))
    # Existing legacy pins survive downgrade; only their exact derived claim
    # mirror is removed, then deterministically reconstructed on re-upgrade.
    migrate('legacy-only-downgrade', 'downgrade', '20261228_0179')
    with migrator.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM kms_data_key_pins')) == 1
        assert db.scalar(text("SELECT to_regclass('public.application_key_version_claims')")) is None
    migrate('legacy-only-reupgrade', 'upgrade', HEAD)
    from app.stock_scrap_security_probe import snapshot
    with migrator.connect() as db:
        catalog = snapshot(db, table_names=TABLES, function_names=FUNCTIONS)
        assert set(catalog['tables']) == set(TABLES)
        assert set(catalog['functions']) == {name+'()' for name in FUNCTIONS}
    (EVIDENCE/'key-provider-catalog.json').write_text(json.dumps(catalog, indent=2, sort_keys=True)+'\n')
    migrator.dispose()
    result = dict(schema='rsc.key-provider.candidate-prepare.v1', result='passed',
        actualPostgreSQLVersion=identity[0], head=HEAD, fullUpgrade=True,
        existingLegacyPinBackfilledExactly=True, legacyOnlyDowngradeReupgrade=True,
        productionReady=False, syntheticOnly=True, source='isolated disposable containers')
    (EVIDENCE/'prepare-receipt.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        safe = str(error) if isinstance(error, RuntimeError) and str(error).endswith('_failed') else type(error).__name__
        print(json.dumps({'result':'failed', 'failure':safe, 'productionReady':False}))
        raise SystemExit(1)
