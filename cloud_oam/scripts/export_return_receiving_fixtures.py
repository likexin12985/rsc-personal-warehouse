#!/usr/bin/env python3
"""Generate four synthetic receiving/inbound samples in a new artifact folder.

Review outputs before copying to frontend/src/test-fixtures/return-receiving.
This invokes real local services on SQLite, not the native PG16 release gate.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

CLOUD=Path(__file__).resolve().parents[1]


def main():
    root=CLOUD/'artifacts/return-receiving-exports';root.mkdir(parents=True,exist_ok=True)
    target=Path(tempfile.mkdtemp(prefix='run-',dir=root))
    environment={k:v for k,v in os.environ.items() if not k.startswith(
        ('PG','OAM_','RSC_PG16_','ALIBABA_CLOUD_','OSS_','AWS_'))}
    environment.update(PYTHONPATH=os.pathsep.join(('backend','scripts')),
        RSC_TEST_RECEIVING_OUTPUT=str(target))
    command=[sys.executable,'-m','pytest','-q','--tb=short',
        'backend/tests/export_loss_receiving_fixture.py',
        'backend/tests/export_work_order_receiving_fixture.py']
    with (target/'export.log').open('wb') as log:
        result=subprocess.run(command,cwd=CLOUD,env=environment,stdout=log,stderr=subprocess.STDOUT,timeout=600)
    expected={f'{origin}-receiving-{mode}.json' for origin in ('loss','work-order') for mode in ('quantity','serial')}
    complete=result.returncode==0 and {p.name for p in target.glob('*.json')}==expected
    print(json.dumps(dict(passed=complete,exitCode=result.returncode,directory=str(target.relative_to(CLOUD)),
        provenance='synthetic real-service SQLite outputs',nativePostgresqlProof=False)),flush=True)
    return 0 if complete else 1


if __name__=='__main__':
    raise SystemExit(main())
