#!/usr/bin/env python3
"""Formal scrap migration, retained history and business checks on owned PG16."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import traceback


CLOUD=Path(__file__).resolve().parents[1]


def manifest():
    """Hash this actual checkout, including isolated copies outside Git discovery."""
    paths = set()
    excluded = {'__pycache__', '.pytest_cache', '.ruff_cache', '.venv', 'artifacts'}
    for name in ('backend', 'edge_sync', 'scripts', 'deployment'):
        for path in (CLOUD / name).rglob('*'):
            if path.is_file() and not excluded.intersection(path.relative_to(CLOUD).parts):
                if path.suffix not in ('.md', '.pyc', '.pyo'):
                    paths.add(path)
    paths.add(CLOUD / 'alembic.ini')
    workflow = CLOUD.parent / '.github/workflows/postgresql16-release-gate.yml'
    if workflow.is_file():
        paths.add(workflow)
    return {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin',required=True)
    parser.add_argument('--tracking',choices=('quantity','serial'),
        help='rerun one tracking mode; the default still checks both')
    parser.add_argument('--transition-readiness-only',action='store_true',
        help='check empty migration roundtrips and complete post-downgrade API startup, without business fixtures')
    parser.add_argument('--runtime-catalog-only',action='store_true',
        help='check formal 0165 Alembic roundtrip and default API catalog admission')
    parser.add_argument('--legacy-history-only',action='store_true',
        help='create true 0164 inverse/correction/key history, migrate it and reread exact requests')
    parser.add_argument('--http-only',action='store_true',
        help='check authenticated mounted HTTP, real commits and recovery for quantity and serial stock')
    parser.add_argument('--profile-functions',action='store_true',
        help='record function statistics on the owned local HTTP test server; no SQL bodies are logged')
    parser.add_argument('--predecessor-source',
        help='preserved 0164 cloud_oam source with copy manifest; prove old API startup after downgrade')
    parser.add_argument('--rollback-source',
        help='explicit reviewed 0164 maintenance rollback build; history still uses pristine predecessor')
    args=parser.parse_args(argv)
    if args.profile_functions and not args.http_only:
        parser.error('function profiling applies only to HTTP checks')
    if sum((args.transition_readiness_only, args.runtime_catalog_only, args.legacy_history_only, args.http_only)) > 1:
        parser.error('readiness, runtime catalog, legacy history and HTTP modes are mutually exclusive')
    if args.tracking and (args.transition_readiness_only or args.runtime_catalog_only):
        parser.error('tracking applies to business and populated history gates')
    if args.predecessor_source and not (args.runtime_catalog_only or args.legacy_history_only):
        parser.error('predecessor source is used only by formal runtime or legacy history gates')
    if args.legacy_history_only and not args.predecessor_source:
        parser.error('legacy history requires preserved predecessor source')
    if args.rollback_source and not ((args.legacy_history_only or args.runtime_catalog_only) and args.predecessor_source):
        parser.error('maintenance rollback source requires runtime catalog or legacy history and original predecessor')
    sys.path[:0]=[str(CLOUD/'backend'),str(CLOUD/'backend/tests'),str(CLOUD.parent)]
    from local_pg16_cluster import native_cluster
    runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
    if args.predecessor_source:
        from pg16_predecessor_runtime import admission as predecessor_admission, populate_history, validate_source
        validate_source(args.predecessor_source)
    rollback_proof = None
    if args.rollback_source:
        from pg16_predecessor_runtime import validate_rollback_pair
        rollback_proof = validate_rollback_pair(args.predecessor_source, args.rollback_source)
    if args.runtime_catalog_only:
        from pg16_scrap_runtime_catalog_gate import release as run
    elif args.legacy_history_only:
        from pg16_scrap_legacy_history_gate import release as run
    elif args.http_only:
        from pg16_scrap_http_business import release as run
    elif args.transition_readiness_only:
        from pg16_scrap_transition_readiness_gate import release as run
    else:
        from pg16_scrap_business_gate import release as run
    sources=manifest();directory=None
    try:
        modes=('quantity',) if (args.transition_readiness_only or args.runtime_catalog_only) else ((args.tracking,) if args.tracking else ('quantity','serial'))
        for tracking in modes:
            with native_cluster(postgres_bin=args.postgres_bin,profile_functions=args.profile_functions,
                    artifact_root=CLOUD/('artifacts/local-scrap-runtime-pg16' if args.runtime_catalog_only else
                        'artifacts/local-scrap-legacy-history-pg16' if args.legacy_history_only else
                        'artifacts/local-scrap-http-pg16' if args.http_only else
                        'artifacts/local-scrap-transition-pg16' if args.transition_readiness_only
                        else 'artifacts/local-scrap-business-pg16')) as (directory,engines):
                (directory/'source-manifest.json').write_text(json.dumps(sources,indent=2)+'\n')
                url=engines['star_oam_migrator'].url.render_as_string(hide_password=False)
                environment=dict(os.environ,OAM_ENVIRONMENT='production',OAM_DATABASE_URL=url,
                    OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator',OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
                def migrate(label,action,revision,expected=None):
                    path=directory/(label+'.log')
                    with path.open('wb') as log:
                        result=subprocess.run([sys.executable,'-m','alembic','-c','alembic.ini',action,revision],
                            cwd=CLOUD,env=environment,stdout=log,stderr=subprocess.STDOUT,timeout=600)
                    if expected: assert result.returncode!=0 and expected in path.read_text(),label
                    else: assert result.returncode==0,label
                    print(label+' PASS',flush=True)
                def provision():
                    command=[str(Path(args.postgres_bin).resolve()/'psql'),'-X','-w','--set=ON_ERROR_STOP=1',
                        '--dbname',url.replace('postgresql+psycopg:','postgresql:',1),'-v','edge_role=edge_inbox']
                    with (directory/'edge-provision.log').open('wb') as log:
                        subprocess.run([*command,'-f',str(CLOUD/'deployment/create_oam_edge_staging.sql')],
                            cwd=CLOUD,env=environment,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=120)
                    from pg16_edge_deployment_gate import assert_edge_deployment_verifier
                    assert_edge_deployment_verifier(command=[*command,'-v','projector_role=star_oam_projector'],
                        environment=environment,evidence_directory=directory)
                extra = {}
                if args.predecessor_source:
                    extra['historical_admission'] = lambda: predecessor_admission(
                        engines, cloud=args.rollback_source or args.predecessor_source, directory=directory)
                if args.legacy_history_only:
                    extra['historical_baseline_admission'] = lambda: predecessor_admission(
                        engines, cloud=args.predecessor_source, directory=directory,
                        label='pristine-0164-before-upgrade')
                    extra['populate_history'] = lambda: populate_history(
                        engines, cloud=args.predecessor_source, directory=directory, tracking=tracking)
                result=run(engines,tracking=tracking,migrate=migrate,provision=provision,**extra)
                if rollback_proof:
                    assert validate_rollback_pair(args.predecessor_source, args.rollback_source) == rollback_proof
                    result['rollbackBuildProof'] = rollback_proof
                result.update(sourceDrift=sorted(set(sources.items())^set(manifest().items())))
                assert result['sourceDrift']==[]
                (directory/'checks.json').write_text(json.dumps(result,indent=2)+'\n')
            state=json.loads((directory/'cluster-state.json').read_text())
            assert {k:state[k] for k in ('status','checks','serverExitCode')}==dict(status='stopped',checks='passed',serverExitCode=0)
            receipt=dict(directory=str(directory.relative_to(CLOUD)),**result)
            (directory/'terminal-v1.json').write_text(json.dumps(receipt,indent=2)+'\n')
            print(json.dumps(receipt),flush=True)
        return 0
    except BaseException as error:
        detail=dict(status='failed',errorType=type(error).__name__,message=str(getattr(error,'orig',error))[:800],
            databaseContext=getattr(getattr(getattr(error,'orig',None),'diag',None),'context',None),
            directory=str(directory.relative_to(CLOUD)) if directory else None,
            frames=[dict(file=f.filename,line=f.lineno,function=f.name) for f in traceback.extract_tb(error.__traceback__)])
        if directory is not None:(directory/'failure.json').write_text(json.dumps(detail,indent=2)+'\n')
        print(json.dumps(detail),flush=True);return 1


if __name__=='__main__':
    raise SystemExit(main())
