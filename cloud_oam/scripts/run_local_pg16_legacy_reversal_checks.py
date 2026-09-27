#!/usr/bin/env python3
"""Actual 0096/0097 history checks on a newly owned local PG16 only."""
import argparse
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
    paths = subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'], cwd=root)
    return {path: hashlib.sha256((root/path).read_bytes()).hexdigest()
            for path in sorted(set(paths.decode().split('\0')))
            if path and not path.endswith('.md') and (root/path).is_file()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    args = parser.parse_args(argv)
    sys.path[:0] = [str(CLOUD/'backend'), str(CLOUD/'backend/tests'), str(CLOUD.parent)]
    from local_pg16_cluster import native_cluster
    runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
    from pg16_work_order_reversal_boundary_gate import assert_legacy_reversal_migration_candidates
    sources = manifest()
    directory = None
    try:
        with native_cluster(postgres_bin=args.postgres_bin,
                artifact_root=CLOUD/'artifacts/local-legacy-reversal-pg16/checks') as (directory, engines):
            (directory/'source-manifest.json').write_text(json.dumps(sources, indent=2)+'\n')
            url = engines['star_oam_migrator'].url.render_as_string(hide_password=False)
            environment = dict(os.environ, OAM_ENVIRONMENT='production', OAM_DATABASE_URL=url,
                OAM_DATABASE_EXPECTED_MIGRATION_ROLE='star_oam_migrator', OAM_DATABASE_EXPECTED_RUNTIME_ROLE='star_oam_api')
            for label, action, revision in (('historical-upgrade','upgrade','20261006_0096'),
                    ('boundary-upgrade','upgrade','20261007_0097'), ('empty-downgrade','downgrade','20261006_0096')):
                with (directory/(label+'.log')).open('wb') as log:
                    subprocess.run([sys.executable,'-m','alembic','-c','alembic.ini',action,revision],
                        cwd=CLOUD, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
            result = assert_legacy_reversal_migration_candidates(engines['star_oam_migrator'])
            result.update(emptyHistoricalRoundtrip=True, sourceDrift=sorted(set(sources.items())^set(manifest().items())),
                productionAcceptance=False, githubReleaseGate=False)
            assert result['sourceDrift'] == []
            (directory/'checks.json').write_text(json.dumps(result, indent=2)+'\n')
        state = json.loads((directory/'cluster-state.json').read_text())
        assert {k:state[k] for k in ('status','checks','serverExitCode')} == dict(status='stopped', checks='passed', serverExitCode=0)
        print(json.dumps(dict(directory=str(directory.relative_to(CLOUD)), **result)), flush=True)
        return 0
    except BaseException as error:
        detail = dict(status='failed', errorType=type(error).__name__, message=str(getattr(error,'orig',error))[:800],
            directory=str(directory.relative_to(CLOUD)) if directory else None,
            frames=[dict(file=f.filename,line=f.lineno,function=f.name) for f in traceback.extract_tb(error.__traceback__)])
        if directory is not None: (directory/'failure.json').write_text(json.dumps(detail,indent=2)+'\n')
        print(json.dumps(detail),flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
