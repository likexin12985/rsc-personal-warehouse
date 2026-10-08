#!/usr/bin/env python3
"""Run candidate invariants on a fresh, owned, socket-only PG16 component DB."""
import argparse
import json
from pathlib import Path
import runpy
import sys

from run_local_pg16_scrap_business_checks import manifest


CLOUD = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin', required=True)
    parser.add_argument('--posting', action='store_true', help='check bidirectional inventory posting edges')
    parser.add_argument('--identity', action='store_true', help='check canonical commands and common operation identity')
    parser.add_argument('--evidence', action='store_true', help='check completed event file bindings and cross-purpose exclusion')
    args = parser.parse_args()
    sys.path[:0] = [str(CLOUD/'backend'), str(CLOUD/'backend/tests')]
    runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
    from local_pg16_cluster import native_cluster
    if args.evidence:
        from pg16_return_condition_evidence_gate import run
    elif args.identity:
        from pg16_return_condition_identity_gate import run
    elif args.posting:
        from pg16_return_condition_posting_gate import run
    else:
        from pg16_return_condition_invariants_gate import run
    sources = manifest()
    with native_cluster(postgres_bin=args.postgres_bin,
            artifact_root=CLOUD/('artifacts/local-return-condition-evidence-pg16' if args.evidence else
                'artifacts/local-return-condition-identity-pg16' if args.identity else
                'artifacts/local-return-condition-posting-pg16' if args.posting
                else 'artifacts/local-return-condition-invariants-pg16')) as (directory, engines):
        print(json.dumps({'directory': str(directory)}), flush=True)
        (directory/'source-manifest.json').write_text(json.dumps(sources, indent=2)+'\n')
        result = run(engines['star_oam_migrator'], engines['star_oam_api'])
        assert sources == manifest(), 'source changed during invariant checks'
        result['sourceFiles'] = len(sources)
        result['sourceUnchanged'] = True
        (directory/'checks.json').write_text(json.dumps(result, indent=2)+'\n')
        print(json.dumps(dict(passed=True, rejected=len(result['rejected']), positive=len(result['positive']),
            races=len(result.get('races', [])), sourceFiles=len(sources))), flush=True)
    state = json.loads((directory/'cluster-state.json').read_text())
    assert state['status']=='stopped' and state['checks']=='passed' and state['serverExitCode']==0
    print('owned PG16 cluster stopped; component gate passed', flush=True)


if __name__ == '__main__':
    main()
