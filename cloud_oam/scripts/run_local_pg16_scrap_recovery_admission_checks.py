#!/usr/bin/env python3
"""Prove the composed recovery admission SQL components in an owned PG16 cluster.

External stock parents are minimal fixtures. This does not migrate a deployed
schema, exercise complete posting, enable production grants, or accept a DSN.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import runpy
import sys
import traceback

CLOUD = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    args = parser.parse_args(argv)
    sys.path[:0] = [str(CLOUD / 'scripts'), str(CLOUD / 'backend'), str(CLOUD / 'backend/tests'), str(CLOUD.parent)]
    from run_local_pg16_stock_loss_sources_checks import manifest
    from local_pg16_cluster import native_cluster
    runpy.run_path(str(CLOUD / 'backend/tests/conftest.py'))
    from pg16_scrap_recovery_admission_gate import run
    source = manifest()
    directory = None
    try:
        with native_cluster(postgres_bin=args.postgres_bin,
                artifact_root=CLOUD / 'artifacts/local-scrap-recovery-admission-pg16') as (directory, engines):
            (directory / 'source-manifest.json').write_text(json.dumps(source, indent=2) + '\n')
            result = dict(startedAt=datetime.now(timezone.utc).isoformat(), **run(engines))
            result['sourceDrift'] = sorted(set(source.items()) ^ set(manifest().items()))
            assert result['sourceDrift'] == []
            (directory / 'checks.json').write_text(json.dumps(result, indent=2) + '\n')
        state = json.loads((directory / 'cluster-state.json').read_text())
        assert {k: state[k] for k in ('status', 'checks', 'serverExitCode')} == dict(
            status='stopped', checks='passed', serverExitCode=0)
        print(json.dumps(dict(directory=str(directory.relative_to(CLOUD)), **result,
            serverExitCode=state['serverExitCode'], githubReleaseGate=False)), flush=True)
        return 0
    except BaseException as error:
        detail = dict(status='failed', errorType=type(error).__name__,
            evidenceDirectory=str(directory.relative_to(CLOUD)) if directory else None,
            frames=[dict(file=Path(frame.filename).name, line=frame.lineno, function=frame.name)
                for frame in traceback.extract_tb(error.__traceback__)])
        if directory is not None:
            (directory / 'failure.json').write_text(json.dumps(detail, indent=2) + '\n')
        print(json.dumps(detail), flush=True)
        raise


if __name__ == '__main__':
    raise SystemExit(main())
