#!/usr/bin/env python3
"""Generate complete synthetic scrap/recovery ledgers in a new artifact folder.

For native component checks only; never a production-data import.
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
    root=CLOUD/'artifacts/scrap-ledger-exports';root.mkdir(parents=True,exist_ok=True)
    target=Path(tempfile.mkdtemp(prefix='run-',dir=root))
    environment={k:v for k,v in os.environ.items() if not k.startswith(
        ('PG','OAM_','RSC_PG16_','ALIBABA_CLOUD_','OSS_','AWS_'))}
    environment.update(PYTHONPATH=os.pathsep.join(('backend','scripts')),
        RSC_TEST_SCRAP_LEDGER_OUTPUT=str(target))
    command=[sys.executable,'-m','pytest','-q','--tb=short',
        'backend/tests/export_scrap_ledger_fixture.py']
    with (target/'export.log').open('wb') as log:
        result=subprocess.run(command,cwd=CLOUD,env=environment,stdout=log,stderr=subprocess.STDOUT,timeout=600)
    expected={'quantity.json','serial.json','quantity-shared.json','serial-shared.json'}
    complete=result.returncode==0 and {p.name for p in target.glob('*.json')}==expected
    print(json.dumps(dict(passed=complete,exitCode=result.returncode,directory=str(target.relative_to(CLOUD)),
        provenance='synthetic real-service SQLite outputs',nativePostgresqlProof=False)),flush=True)
    return 0 if complete else 1


if __name__=='__main__':
    raise SystemExit(main())
