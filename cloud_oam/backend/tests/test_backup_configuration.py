"""Invalid total budgets must refuse before any remote backup is started."""
from backup_test_support import CLOUD, WORKER, OPS
from pathlib import Path
import os,shutil,subprocess,sys
import pytest
HERE=Path(__file__).resolve().parent


@pytest.mark.parametrize('timeout',[None,'0','-1','1.5','86401','nan','01'])
def test_invalid_total_budget_never_invokes_remote_transport(tmp_path,timeout):
    project=tmp_path/'project';(project/'scripts').mkdir(parents=True);(project/'deployment/backup').mkdir(parents=True)
    shutil.copyfile(CLOUD/'scripts/backup.sh',project/'scripts/backup.sh')
    shutil.copyfile(OPS/'capacity.sh',project/'deployment/backup/capacity.sh')
    binary=tmp_path/'bin';binary.mkdir();marker=tmp_path/'remote-started'
    (binary/'docker').write_text('#!/bin/sh\ntouch '+str(marker)+'\nexit 99\n');(binary/'docker').chmod(0o700)
    (binary/'python3').symlink_to(sys.executable)
    backups=tmp_path/'backups'
    env={'PATH':str(binary)+':/usr/bin:/bin:/usr/sbin:/sbin','OAM_DB_BACKUP_PASSWORD':'synthetic-not-real',
        'POSTGRES_DB':'synthetic','RSC_OSS_BACKUP_REGION':'cn-hangzhou','RSC_OSS_BACKUP_BUCKET':'synthetic',
        'RSC_BACKUP_MAXIMUM_BYTES':'1048576','BACKUP_DIR':str(backups)}
    if timeout is not None:env['RSC_BACKUP_TIMEOUT_SECONDS']=timeout
    run=subprocess.run(['/bin/sh',str(project/'scripts/backup.sh')],env=env,capture_output=True,timeout=10)
    assert run.returncode!=0 and not marker.exists()
    assert not list(backups.glob('.rsc-deadline-*')) and b'synthetic-not-real' not in run.stderr
