#!/usr/bin/env python3
"""Check original-cursor scrap/recovery plans in owned native PG16 clusters using synthetic exports.

Never accepts a DSN. The complete model schema is created from code; this is
not the production migration catalog or a native business-service execution.
"""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys
import traceback
from run_local_pg16_stock_loss_sources_checks import manifest

CLOUD=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--postgres-bin',required=True)
    parser.add_argument('--fixture-directory',required=True)
    args=parser.parse_args()
    fixture=Path(args.fixture_directory).resolve(strict=True)
    assert fixture.is_relative_to((CLOUD/'artifacts/scrap-ledger-exports').resolve())
    names={p.name for p in fixture.glob('*.json')}
    single={'quantity.json','serial.json'}
    assert names in (single, single|{'quantity-shared.json','serial-shared.json'})
    sys.path[:0]=[str(CLOUD/'backend'),str(CLOUD/'backend/tests')]
    runpy.run_path(str(CLOUD/'backend/tests/conftest.py'))
    from local_pg16_cluster import native_cluster
    from pg16_scrap_historical_plans_gate import run
    sources=manifest();directory=None;reports=[]
    try:
        for filename in sorted(names):
            with native_cluster(postgres_bin=args.postgres_bin,
                    artifact_root=CLOUD/'artifacts/local-scrap-historical-plans-pg16') as (directory,engines):
                (directory/'source-manifest.json').write_text(json.dumps(sources,indent=2)+'\n')
                data=fixture/filename
                result=run(engines,data)
                result.update(fixture=str(data.relative_to(CLOUD)),fixtureSha256=hashlib.sha256(data.read_bytes()).hexdigest(),
                    sourceDrift=sorted(set(sources.items())^set(manifest().items())))
                assert result['sourceDrift']==[]
                (directory/'checks.json').write_text(json.dumps(result,indent=2)+'\n')
            state=json.loads((directory/'cluster-state.json').read_text())
            assert {k:state[k] for k in ('status','checks','serverExitCode')}==dict(status='stopped',checks='passed',serverExitCode=0)
            reports.append(dict(directory=str(directory.relative_to(CLOUD)),**result))
            print(json.dumps(reports[-1]),flush=True)
        return 0
    except BaseException as error:
        detail=dict(status='failed',errorType=type(error).__name__,directory=str(directory.relative_to(CLOUD)) if directory else None)
        if directory is not None:(directory/'failure.json').write_text(json.dumps(detail,indent=2)+'\n')
        print(json.dumps(detail),flush=True);traceback.print_exc()
        return 1


if __name__=='__main__':
    raise SystemExit(main())
