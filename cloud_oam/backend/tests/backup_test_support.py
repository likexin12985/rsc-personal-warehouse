"""Owned test transports for the formal backup entry; no Docker or cloud access."""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys

CLOUD = Path(__file__).resolve().parents[2]
WORKER = CLOUD / 'deployment/backup-worker'
OPS = CLOUD / 'deployment/backup'
sys.path.insert(0, str(WORKER))


def simulate_entry(tmp_path, *, failure):
    project = tmp_path / 'project'
    for relative in ['scripts/backup.sh', 'scripts/backup_steps.sh',
                     'deployment/backup/capacity.sh',
                     'deployment/backup-worker/deadline_runner.py',
                     'deployment/backup-worker/lease_sender.py']:
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(CLOUD / relative, target)
    binary = tmp_path / 'bin'
    binary.mkdir()
    (binary / 'python3').symlink_to(sys.executable)
    transport = binary / 'docker'
    # This fixture replaces the Docker boundary only. The real shell entry,
    # process supervisor, lease sender and publication/cleanup code still run.
    transport.write_text('#!' + sys.executable + '\n' + '''import hashlib,json,os,sys
from pathlib import Path
a=sys.argv[1:];failure=os.environ['TEST_BACKUP_FAILURE']
if 'object-backup' in a:
 if '--preflight' in a:raise SystemExit(0)
 job=Path(a[a.index('--volume')+1].removesuffix(':/job:rw'))
 data=b'synthetic verified transport output'
 (job/'verified-joint.tar').write_bytes(data)
 (job/'backup-complete.json').write_text('{}')
 (job/'verified-joint.sha256').write_text(hashlib.sha256(data).hexdigest()+'  verified-joint.tar\\n')
elif 'db' in a:
 if failure=='dump':raise SystemExit(87)
 os.write(1,b'synthetic snapshot transport')
elif 'api' in a:
 if failure=='uploads':raise SystemExit(88)
 os.write(1,b'synthetic attachment transport')
else:raise SystemExit(64)
''')
    transport.chmod(0o700)
    checksum = binary / 'sha256sum'
    checksum.write_text('#!' + sys.executable + '\n' + '''import hashlib,sys
from pathlib import Path
assert sys.argv[1:]==['-c','verified-joint.sha256']
assert Path('verified-joint.sha256').read_text()==hashlib.sha256(Path('verified-joint.tar').read_bytes()).hexdigest()+'  verified-joint.tar\\n'
''')
    checksum.chmod(0o700)
    if failure == 'publication':
        link = binary / 'ln'
        link.write_text('#!/bin/sh\nexit 73\n')
        link.chmod(0o700)
    backups = tmp_path / 'backups'
    backups.mkdir()
    previous = backups / 'backup_previous.tar'
    previous.write_bytes(b'previous complete synthetic backup')
    env = dict(PATH=str(binary)+':/usr/bin:/bin:/usr/sbin:/sbin',
               OAM_DB_BACKUP_PASSWORD='synthetic-backup-password', POSTGRES_DB='synthetic_backup',
               RSC_OSS_BACKUP_REGION='cn-hangzhou', RSC_OSS_BACKUP_BUCKET='synthetic-backup',
               RSC_BACKUP_MAXIMUM_BYTES='1048576', RSC_BACKUP_TIMEOUT_SECONDS='20',
               BACKUP_DIR=str(backups), TEST_BACKUP_FAILURE=failure)
    run = subprocess.run(['/bin/sh', str(project/'scripts/backup.sh')], cwd=project,
                         env=env, capture_output=True, timeout=30)
    assert b'synthetic-backup-password' not in run.stdout + run.stderr
    return run, backups, previous
