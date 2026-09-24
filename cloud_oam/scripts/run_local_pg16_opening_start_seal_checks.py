#!/usr/bin/env python3
"""Build-independent local original request seal checks in a new owned native PG16 cluster.

This command accepts a PG16 binary directory only. It creates and retains its
own data directory and Unix socket, shuts down only its child server, and does
not run or enable the GitHub-only destructive release gate. No existing DSN,
remote host, port, database or data directory can be supplied.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback
from dataclasses import asdict

CLOUD=Path(__file__).resolve().parents[1]


def source_fingerprints():
    files = {path for folder in ('backend/app','backend/alembic','backend/tests')
             for path in (CLOUD/folder).rglob('*.py')}
    files.update((CLOUD/'deployment').glob('*.sql'))
    files.add(Path(__file__).resolve())
    return {str(path.relative_to(CLOUD)):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(files)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin',required=True)
    args=parser.parse_args(argv)
    sys.path.insert(0,str(CLOUD/'backend'))
    sys.path.insert(0,str(CLOUD/'backend/tests'))
    from local_pg16_cluster import native_cluster
    report=None
    try:
        with native_cluster(postgres_bin=args.postgres_bin,artifact_root=CLOUD/'artifacts/opening-start-seals-20260920/checks') as (directory,engines):
            report=directory
            sources=source_fingerprints()
            (directory/'source-sha256.json').write_text(json.dumps(sources,indent=2,sort_keys=True)+'\n')
            print('Verified new private PG16 cluster: '+str(directory),flush=True)
            # A real production migration path, as its distinct owner. No
            # credentials are needed on this process-owned private Unix socket.
            url=engines['star_oam_migrator'].url.render_as_string(hide_password=False)
            environment=os.environ.copy()
            environment.update(OAM_ENVIRONMENT='production',OAM_DATABASE_URL=url,
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
            for label, command in (('migration', ['upgrade','head']),
                ('empty-down-0128',['downgrade','20261106_0127']),('empty-up-0128',['upgrade','head'])):
                with (directory/(label+'.log')).open('wb') as output:
                    result=subprocess.run([sys.executable,'-m','alembic','-c','alembic.ini',*command],cwd=CLOUD,
                        env=environment,stdout=output,stderr=subprocess.STDOUT,timeout=180)
                if result.returncode:raise RuntimeError('local_pg16_migration_failed')
            print('Real PG16 migration to current head passed',flush=True)
            # Apply the existing deployment ACL script as the schema owner to
            # this factory-owned database; migrations alone do not provision
            # the inventory staging receiver's column-level grants.
            with (directory/'edge-provision.log').open('wb') as output:
                subprocess.run([str(Path(args.postgres_bin).resolve()/'psql'),'-X',
                    '--dbname',url.replace('postgresql+psycopg:','postgresql:',1),
                    '-v','edge_role=edge_inbox','-f',str(CLOUD/'deployment/create_oam_edge_staging.sql')],
                    cwd=CLOUD,env=environment,stdout=output,stderr=subprocess.STDOUT,timeout=60,check=True)
            # Mirror the test process's synthetic application configuration;
            # every database operation below uses one of the explicit engines.
            runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
            from pg16_opening_start_seal_gate import run
            result=run(engines)
            from pg16_opening_start_seal_gate import snapshot as all_reconciliation_facts
            from sqlalchemy import text
            before=all_reconciliation_facts(engines['star_oam_migrator'])
            with (directory/'populated-down-refused-0128.log').open('wb') as output:
                refused=subprocess.run([sys.executable,'-m','alembic','-c','alembic.ini','downgrade','20261106_0127'],cwd=CLOUD,
                    env=environment,stdout=output,stderr=subprocess.STDOUT,timeout=180)
            if refused.returncode == 0 or 'original request seals must be retained' not in (directory/'populated-down-refused-0128.log').read_text():
                raise RuntimeError('populated downgrade must refuse before dropping original seals')
            if all_reconciliation_facts(engines['star_oam_migrator'])!=before:
                raise RuntimeError('0128 refused downgrade changed immutable facts')
            with engines['star_oam_migrator'].connect() as db:
                if db.scalar(text('SELECT version_num FROM alembic_version'))!='20261107_0128':
                    raise RuntimeError('0128 final revision mismatch')
            result['populatedDowngradeRefusedAndFactsPreserved']=True
            if source_fingerprints()!=sources:
                raise RuntimeError('source changed during native checks; discard this run and retry from a stable source')
            result['sourceFiles']=len(sources)
            result['sourceManifestSha256']=hashlib.sha256((directory/'source-sha256.json').read_bytes()).hexdigest()
            result['sourceStableDuringRun']=True
            (directory/'checks.json').write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps(result,sort_keys=True),flush=True)
    except BaseException as error:
        # DB exceptions may carry SQL or credentials; details stay in the
        # retained local server/migration log, not the user-facing stream.
        details=dict(status='failed',errorType=type(error).__name__,evidenceDirectory=str(report) if report else None,
            frames=[dict(file=frame.filename,line=frame.lineno,function=frame.name) for frame in traceback.extract_tb(error.__traceback__)])
        from pg16_release_gate_diagnostics import SanitizedPostgreSQLDiagnosticError
        if isinstance(error,SanitizedPostgreSQLDiagnosticError):details['diagnostic']=asdict(error.diagnostic)
        security_module=sys.modules.get('app.database_security')
        if security_module is not None and isinstance(error,security_module.DatabaseSecurityBoundaryError):
            # This type contains the validator's fixed catalog-coordinate labels,
            # never a driver exception, statement, parameter or credential.
            details['securityBoundary']=str(error)
        if report is not None:(report/'failure.json').write_text(json.dumps(details,indent=2)+'\n')
        print(json.dumps(details),flush=True)
        return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
