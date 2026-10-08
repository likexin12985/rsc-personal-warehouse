#!/usr/bin/env python3
"""Candidate forward history over full 0164 on newly owned PG16 only."""
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
    parser.add_argument('--scenario',choices=('generations','seal_retention'),default='generations')
    parser.add_argument('--components',choices=('history','all'),default='all',
        help='all installs the complete candidate after the first legacy round; history reproduces the earlier history-only gate')
    args=parser.parse_args(argv)
    sys.path[:0]=[str(CLOUD/'backend'),str(CLOUD/'backend/tests'),str(CLOUD.parent)]
    from local_pg16_cluster import native_cluster
    runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
    from pg16_scrap_forward_history_gate import release as run
    sources=manifest();directory=None
    try:
        for tracking in ((args.tracking,) if args.tracking else ('quantity','serial')):
            with native_cluster(postgres_bin=args.postgres_bin,
                    artifact_root=CLOUD/'artifacts/local-scrap-forward-history-pg16') as (directory,engines):
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
                result=run(engines,tracking=tracking,migrate=migrate,provision=provision,
                    scenario=args.scenario,components=args.components)
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
