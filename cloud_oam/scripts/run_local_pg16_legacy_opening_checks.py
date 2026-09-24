#!/usr/bin/env python3
"""Verify frozen historical facts on new owned PG16 clusters only.

Accepts no DSN, existing directory or remote host. This is a bounded historical
migration check, never the GitHub-only destructive release gate.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback

CLOUD = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin',required=True)
    args = parser.parse_args(argv)
    sys.path[:0] = [str(CLOUD/'backend'),str(CLOUD/'backend/tests')]
    from local_pg16_cluster import native_cluster
    from pg16_legacy_opening_fixture import seed_legacy_completion,LEGACY_REVISION
    from pg16_legacy_opening_gate import snapshot,legacy_catalog,historical_facts,assert_backfill
    from sqlalchemy import text
    out = CLOUD/'artifacts/opening-legacy-fixture-20260920/checks'
    report = None
    try:
        for case in ('backfill','direct-head','rejection'):
            negative = case=='rejection'
            with native_cluster(postgres_bin=args.postgres_bin,artifact_root=out) as (directory,engines):
                report = directory;owner=engines['star_oam_migrator']
                print('Verified owned PG16 '+directory.name+'; case='+case,flush=True)
                environment=os.environ.copy()
                environment.update(OAM_ENVIRONMENT='production',OAM_DATABASE_URL=owner.url.render_as_string(hide_password=False),
                    OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
                def migrate(label,*command,success=True):
                    path=directory/(label+'.log')
                    with path.open('wb') as log:
                        result=subprocess.run([sys.executable,'-m','alembic','-c','alembic.ini',*command],cwd=CLOUD,
                            env=environment,stdout=log,stderr=subprocess.STDOUT,timeout=300)
                    assert (result.returncode==0)==success,label
                    return path.read_text()
                migrate('migration-0051','upgrade',LEGACY_REVISION)
                evidence=seed_legacy_completion(owner,mutate_policy_after_completion=negative)
                print('0051 frozen facts loaded with all later columns absent',flush=True)
                if negative:
                    before=snapshot(owner);catalog=legacy_catalog(owner)
                    output=migrate('rejected-upgrade','upgrade','head',success=False)
                    old=runpy.run_path(str(CLOUD/'backend/alembic/versions/20260903_0052_opening_terminal_guard_execution.py'))
                    assert old['REQUEST_EVIDENCE_ERROR'] in output or old['EXISTING_ROWS_ERROR'] in output
                    assert snapshot(owner)==before and legacy_catalog(owner)==catalog
                    with owner.connect() as db:
                        assert db.scalar(text('SELECT version_num FROM alembic_version'))==LEGACY_REVISION
                    print('Changed historical policy: upgrade refused; all rows and catalog unchanged PASS',flush=True)
                else:
                    before=historical_facts(owner)
                    if case=='backfill':
                        migrate('upgrade-0052','upgrade','20260903_0052')
                        assert_backfill(owner,evidence)
                        assert historical_facts(owner,columns=before)==before
                        # An active historical opening cannot lose its new evidence.
                        state=snapshot(owner);catalog=legacy_catalog(owner)
                        migrate('active-downgrade-refused','downgrade',LEGACY_REVISION,success=False)
                        assert snapshot(owner)==state and legacy_catalog(owner)==catalog
                        print('0052 exact request/resolution/SN alias backfill; active downgrade refused PASS',flush=True)
                    migrate('upgrade-current','upgrade','head')
                    assert_backfill(owner,evidence)
                    assert historical_facts(owner,columns=before)==before
                    with owner.connect() as db:
                        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='20261107_0128'
                        assert db.scalar(text('SELECT opening_authorization_version FROM stocktake_tasks WHERE id=:id'),
                            {'id':evidence['task_id']}) is None
                    runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
                    from app.database_security import validate_production_database_security
                    validate_production_database_security(engines['star_oam_api'],expected_runtime_role='star_oam_api',
                        expected_migration_role='star_oam_migrator')
                    print('Current head: historical facts and unknown authorization preserved; API boundary PASS',flush=True)
                result=dict(status='passed',actualPostgreSQL16=True,case=case,
                    finalRevision=LEGACY_REVISION if negative else '20261107_0128',frozenLegacyFacts=True,
                    noCurrentServiceUsed=True,fullReleaseGate=False,productionAcceptance=False)
                (directory/'checks.json').write_text(json.dumps(result,indent=2)+'\n')
        return 0
    except BaseException as error:
        details=dict(status='failed',errorType=type(error).__name__,evidenceDirectory=str(report) if report else None,
            frames=[dict(file=f.filename,line=f.lineno,function=f.name) for f in traceback.extract_tb(error.__traceback__)])
        if report is not None:(report/'failure.json').write_text(json.dumps(details,indent=2)+'\n')
        print(json.dumps(details),flush=True)
        return 1


if __name__=='__main__':raise SystemExit(main())
