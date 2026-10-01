#!/usr/bin/env python3
"""Prove independent audit isolation and stock/seal fencing on a new owned PG16 cluster, never a DSN."""
import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback

CLOUD = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    args = parser.parse_args()
    sys.path[:0] = [str(CLOUD/'backend'), str(CLOUD/'backend/tests')]
    from local_pg16_cluster import native_cluster
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from sqlalchemy.exc import DBAPIError
    directory = None
    try:
        with native_cluster(postgres_bin=args.postgres_bin,
                            artifact_root=CLOUD/'artifacts/authentication-fence-pg16') as (directory, engines):
            owner = engines['star_oam_migrator']
            environment = dict(os.environ, OAM_ENVIRONMENT='production',
                OAM_DATABASE_URL=owner.url.render_as_string(hide_password=False),
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',
                OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')

            def migrate(label, action, target, expected_error=None):
                path = directory/(label+'.log')
                with path.open('wb') as log:
                    result = subprocess.run([sys.executable, '-m', 'alembic', '-c', 'alembic.ini', action, target],
                        cwd=CLOUD, env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=600)
                if expected_error:
                    assert result.returncode != 0 and expected_error in path.read_text(), label
                else:
                    assert result.returncode == 0, label

            def snapshot():
                with owner.connect() as db:
                    names = db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' "
                                             "AND tablename<>'alembic_version' ORDER BY tablename")).all()
                    return {name: db.scalar(text("SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) "
                                                  'FROM public."'+name+'" t')) for name in names}

            migrate('old-head', 'upgrade', '20261212_0163')
            runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
            from app.models import User
            from pg16_loss_execution_auth_isolation import verify as old_probe
            from pg16_authentication_fence_gate import verify
            from app.database_security import validate_production_database_security
            with Session(owner) as db:
                user = User(mobile='synthetic-auth-fence', name='Synthetic authentication fixture',
                            password_hash='disabled', account_status='active')
                db.add(user); db.commit(); user_id = user.id
            try:
                old_probe(engines, user_id=user_id)
            except DBAPIError as error:
                assert error.orig.sqlstate == '55P03'
                assert 'rsc_fence_loss_correction_seal_0161' in str(error.orig)
            else:
                raise AssertionError('0163 regression was not reproduced')
            before = snapshot()
            # A changed predecessor must be rejected atomically, not silently
            # overwritten. Restore only our fixture's exact function bytes.
            with owner.begin() as db:
                original = db.scalar(text("SELECT pg_get_functiondef('public.rsc_fence_loss_correction_seal_0161()'::regprocedure)"))
                drifted = original.replace('BEGIN\n', 'BEGIN\n    -- synthetic source drift\n', 1)
                assert drifted != original
                db.execute(text(drifted))
            migrate('drift-refused', 'upgrade', 'head', '0164 exact function source, ownership or ACL drift')
            assert snapshot() == before
            with owner.begin() as db:
                assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261212_0163'
                assert db.scalar(text("SELECT pg_get_functiondef('public.rsc_fence_loss_correction_seal_0161()'::regprocedure)")) == drifted
                db.execute(text(original))
            migrate('forward', 'upgrade', 'head')
            assert snapshot() == before
            with owner.connect() as db:
                assert db.scalar(text('SELECT version_num FROM alembic_version')) == '20261213_0164'
            validate_production_database_security(engines['star_oam_api'],
                expected_runtime_role='star_oam_api', expected_migration_role='star_oam_migrator')
            proof = verify(engines, user_id=user_id)
            retained = snapshot()
            migrate('downgrade', 'downgrade', '20261212_0163')
            assert snapshot() == retained
            with owner.connect() as db:
                assert db.scalar(text("SELECT pg_get_functiondef('public.rsc_fence_loss_correction_seal_0161()'::regprocedure)")) == original
            migrate('reupgrade', 'upgrade', 'head')
            assert snapshot() == retained
            validate_production_database_security(engines['star_oam_api'],
                expected_runtime_role='star_oam_api', expected_migration_role='star_oam_migrator')
            result = dict(status='passed', head='20261213_0164', oldDefectReproduced=True,
                          predecessorDriftRejectedAtomically=True, allBusinessRowsRetained=True,
                          populatedRoundTrip=True, fullStartupAndAcl=True, proof=proof,
                          productionAcceptance=False)
            (directory/'checks.json').write_text(json.dumps(result, indent=2)+'\n')
            print(json.dumps(dict(status='passed', evidenceDirectory=str(directory))), flush=True)
        return 0
    except BaseException as error:
        result = dict(status='failed', errorType=type(error).__name__,
                      frames=[dict(file=f.filename, line=f.lineno, function=f.name)
                              for f in traceback.extract_tb(error.__traceback__)])
        if directory is not None:
            (directory/'failure.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(dict(result, evidenceDirectory=str(directory))), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
