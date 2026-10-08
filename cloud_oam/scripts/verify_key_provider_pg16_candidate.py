#!/usr/bin/env python3
"""One phase, once, on the externally verified disposable C1 PG16 pair.

No database discovery, deployment, bootstrap, retry or production credentials.
After failure/interruption, read the exact receipt and database before recovery.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

WORKSPACE = Path('/workspace')
EVIDENCE = Path('/evidence')
HEAD = '20261229_0180'
ROLES = ('star_oam_migrator', 'star_oam_api', 'star_oam_backup', 'star_oam_projector', 'edge_inbox')
PHASES = ('startup', 'startup-import-fixed', 'catalog', 'legacy-downgrade-drift',
          'bindings', 'pin-reader', 'downgrade', 'readback')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def limits(db):
    db.execute(text("SET LOCAL statement_timeout='45s'"))
    db.execute(text("SET LOCAL lock_timeout='5s'"))


def identity(engines):
    for role, engine in engines.items():
        with engine.connect() as db:
            limits(db)
            row = db.execute(text("SELECT current_database(),current_user,session_user,current_setting('server_version_num')::int,current_setting('listen_addresses')")).one()
            require(tuple(row[:3]) == ('rsc_pg16_release_gate', role, role)
                    and 160000 <= row[3] < 170000 and row[4] == '', 'isolated direct identity required')
    with engines[ROLES[0]].connect() as db:
        limits(db)
        require(db.execute(text('SELECT version_num FROM alembic_version')).scalars().all() == [HEAD], 'exact head required')


def catalog_gate(engines):
    from app.key_provider_binding_security import verify
    # Each drift exists only in this owned synthetic transaction and is rolled
    # back before authoritative reread. No weakened ACL/trigger is committed.
    cases = {
        'api-insert': ["GRANT INSERT ON openbao_data_key_pins TO star_oam_api"],
        'backup-update': ["GRANT UPDATE ON application_key_version_claims TO star_oam_backup"],
        'public-select': ["GRANT SELECT ON openbao_data_key_pins TO PUBLIC"],
        'column-update': ["GRANT UPDATE(ciphertext_sha256) ON openbao_data_key_pins TO star_oam_api"],
        'disabled-trigger': ["ALTER TABLE kms_data_key_pins DISABLE TRIGGER trg_kms_data_key_pins_claim_0180"],
        'missing-trigger': ["DROP TRIGGER trg_openbao_data_key_pins_claim_0180 ON openbao_data_key_pins"],
        'extra-overload': ["CREATE FUNCTION public.rsc_claim_application_key_version_0180(integer) RETURNS integer LANGUAGE sql AS 'SELECT $1'"],
        'function-execute': ["GRANT EXECUTE ON FUNCTION public.rsc_guard_application_key_version_claim_0180() TO star_oam_api"],
        'column-default': ["ALTER TABLE openbao_data_key_pins ALTER COLUMN environment SET DEFAULT 'test'"],
        'missing-check': ["ALTER TABLE openbao_data_key_pins DROP CONSTRAINT ck_openbao_data_key_pins_key_path_0180"],
        'missing-unique': ["ALTER TABLE application_key_version_claims DROP CONSTRAINT pk_application_key_version_claims_0180"],
        'missing-claim': [
            "ALTER TABLE application_key_version_claims DISABLE TRIGGER trg_application_key_version_claims_immutable_0180",
            "DELETE FROM application_key_version_claims WHERE application_key_version=1 AND purpose='authentication_idempotency'",
            "ALTER TABLE application_key_version_claims ENABLE ALWAYS TRIGGER trg_application_key_version_claims_immutable_0180"],
        'claim-time-mismatch': [
            "ALTER TABLE application_key_version_claims DISABLE TRIGGER trg_application_key_version_claims_immutable_0180",
            "UPDATE application_key_version_claims SET created_at=created_at+interval '1 second' WHERE application_key_version=1 AND purpose='authentication_idempotency'",
            "ALTER TABLE application_key_version_claims ENABLE ALWAYS TRIGGER trg_application_key_version_claims_immutable_0180"],
    }
    with engines['star_oam_api'].connect() as db:
        limits(db)
        verify(db)
    for name, statements in cases.items():
        with engines[ROLES[0]].connect() as db:
            transaction = db.begin()
            try:
                limits(db)
                for statement in statements:
                    db.execute(text(statement))
                rejected = False
                try:
                    verify(db)
                except ValueError as error:
                    expected = '0180 key-provider pin/claim correspondence mismatch' if name in {'missing-claim', 'claim-time-mismatch'} else '0180 key-provider full catalog mismatch'
                    require(str(error) == expected, 'catalog failure classification')
                    rejected = True
                require(rejected, 'catalog drift was admitted')
            finally:
                transaction.rollback()
        with engines['star_oam_api'].connect() as db:
            limits(db)
            verify(db)
    return dict(rejectedDrifts=list(cases), exactCatalogAndFactsRestoredAfterEveryCase=True)


def run_phase(phase, engines):
    from app.key_provider_binding_security import verify
    if phase in {'startup', 'startup-import-fixed'}:
        from app.database_security import validate_production_database_security
        validate_production_database_security(engines['star_oam_api'],
            expected_runtime_role='star_oam_api', expected_migration_role='star_oam_migrator')
        return dict(fullProductionDatabaseSecurityValidator=True)
    if phase == 'catalog':
        return catalog_gate(engines)
    if phase == 'bindings':
        from pg16_key_provider_binding_gate import run
        return run(engines)
    if phase == 'legacy-downgrade-drift':
        from pg16_key_provider_legacy_downgrade_gate import run
        return run(engines)
    if phase == 'pin-reader':
        from pg16_openbao_pin_reader_candidate_gate import run
        return run(engines)
    if phase == 'downgrade':
        from pg16_key_provider_binding_gate import _snapshot
        before = _snapshot(engines[ROLES[0]])
        require(bool(before['openbao_data_key_pins']), 'downgrade fixture requires OpenBao pins')
        environment = dict(os.environ,
            OAM_DATABASE_URL=str(engines[ROLES[0]].url), OAM_ENVIRONMENT='production',
            OAM_DATABASE_EXPECTED_MIGRATION_ROLE=ROLES[0], OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api',
            OAM_MIGRATION_CACHE_EXECUTION='1')
        path = EVIDENCE/'openbao-downgrade-denial.log'
        with path.open('xb') as log:
            result = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'alembic.ini',
                'downgrade', '20261228_0179'], cwd=WORKSPACE, env=environment,
                stdout=log, stderr=subprocess.STDOUT, timeout=180)
        require(result.returncode != 0 and 'cannot downgrade 0180 while OpenBao pins or non-exact legacy claims exist' in path.read_text(), 'exact downgrade rejection required')
        identity(engines)
        require(_snapshot(engines[ROLES[0]]) == before, 'blocked downgrade changed facts')
        with engines['star_oam_api'].connect() as db:
            limits(db)
            verify(db)
        return dict(openBaoDowngradeBlocked=True, allFactsHeadAndCatalogPreserved=True)
    require(phase == 'readback', 'unknown phase')
    with engines['star_oam_api'].connect() as db:
        db = db.execution_options(isolation_level='REPEATABLE READ')
        db.execute(text('SET TRANSACTION READ ONLY'))
        limits(db)
        verify(db)
        counts = {name: db.scalar(text('SELECT count(*) FROM public.'+name)) for name in
                  ('kms_data_key_pins', 'openbao_data_key_pins', 'application_key_version_claims')}
    return dict(exactImmutableCatalog=True, pinClaimCorrespondence=True, counts=counts)


def main():
    if not __debug__ or os.environ.get('PYTHONOPTIMIZE'):
        raise RuntimeError('unoptimized candidate interpreter required')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=PHASES, required=True)
    phase = parser.parse_args().phase
    require(os.geteuid() == 70 and WORKSPACE.is_dir() and EVIDENCE.is_dir(), 'isolated candidate context required')
    require(not any(name.startswith(('PG', 'OAM_', 'ALIBABA_', 'BAO_', 'VAULT_')) for name in os.environ), 'unconfigured candidate environment required')
    prepare = json.loads((EVIDENCE/'prepare-receipt.json').read_text())
    require(prepare['result'] == 'passed' and prepare['head'] == HEAD and prepare['syntheticOnly'] is True, 'prepare evidence required')
    # A prior start, including unknown outcome, prevents blind replay.
    with (EVIDENCE/(phase+'-started.json')).open('x') as marker:
        json.dump(dict(phase=phase, state='started_once'), marker)
    sys.path[:0] = [str(WORKSPACE/'backend'), str(WORKSPACE/'backend/tests')]
    # Model imports construct Settings before the explicit validator is called.
    # Supply only this owned synthetic API URL; this is not API readiness or a
    # production Settings validation. The validator itself has no test bypass.
    os.environ.update(OAM_ENVIRONMENT='test',
        OAM_DATABASE_URL='postgresql+psycopg://star_oam_api@/rsc_pg16_release_gate?host=/pgsocket')
    engines = {role: create_engine('postgresql+psycopg://'+role+'@/rsc_pg16_release_gate?host=/pgsocket',
        poolclass=NullPool, hide_parameters=True, connect_args={'connect_timeout':5}) for role in ROLES}
    report = dict(schema='rsc.key-provider.candidate-runtime.v1', phase=phase, head=HEAD,
                  syntheticOnly=True, productionReady=False)
    try:
        identity(engines)
        report.update(result='passed', evidence=run_phase(phase, engines))
    except Exception as error:
        report.update(result='failed', failureType=type(error).__name__)
        # Fixed gate identifiers only; never retain driver SQL or parameters.
        if type(error).__name__ in {'KeyProviderBindingGateError', 'OpenBaoPinReaderGateError',
                                  'KeyProviderLegacyDowngradeGateError'}:
            report['failureStage'] = str(error)
    finally:
        for engine in engines.values():
            engine.dispose()
    (EVIDENCE/(phase+'-receipt.json')).write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
