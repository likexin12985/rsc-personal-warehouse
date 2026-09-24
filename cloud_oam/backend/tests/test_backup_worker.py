from backup_test_support import CLOUD, WORKER, OPS
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import ast,gzip,hashlib,json,os,subprocess,sys,tarfile

import pytest

import backup_worker as worker
import formal_file_integrity as pure
from joint_backup import unpack_exact,JointBackupError
from test_backup_joint import setup_case,tar_bytes
from test_backup_objects import fixture_row,no_network
from app.formal_services import formal_files as production


@pytest.fixture
def job(tmp_path,monkeypatch):
    for name in list(os.environ):
        if name.startswith(('OAM_','PG','POSTGRES_')):monkeypatch.delenv(name)
    directory=tmp_path/'job';directory.mkdir(mode=0o700)
    export,legacy,reader,transport=setup_case(directory)
    export.rename(directory/'snapshot-export.tar')
    for path in directory.iterdir():path.chmod(0o600)
    return directory,reader,transport


def execute(job,**kwargs):
    directory,reader,_=job
    return worker.pack(directory,region='cn-hangzhou',bucket='synthetic-backup',maximum_bytes=1024*1024,
        reader_factory=lambda **kw:reader,**kwargs)


def test_worker_full_private_pack_verify_and_receipt(job):
    directory,_,transport=job
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
    receipt=execute(job)
    assert receipt['status']=='verified' and receipt['cloud_restore_verified'] is False
    assert receipt==json.loads((directory/'backup-complete.json').read_text())
    assert (directory/'verified-joint.sha256').read_text()==receipt['sha256']+'  verified-joint.tar\n'
    assert len(transport.calls)==2 and all(p.stat().st_mode&0o077==0 for p in directory.iterdir())
    assert {name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in before}==before
    assert not any(p.is_dir() for p in directory.iterdir())
    current={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}
    with pytest.raises(worker.BackupWorkerError,match='job_inputs_not_exact'):execute(job)
    assert {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.iterdir()}==current


@pytest.mark.parametrize('name',['OAM_DATABASE_URL','OAM_JWT_SECRET','PGPASSWORD','PGHOST','POSTGRES_DB','POSTGRES_PASSWORD'])
def test_application_database_environment_never_reaches_reader(job,monkeypatch,name):
    directory,_,transport=job;monkeypatch.setenv(name,'synthetic-private-value')
    with pytest.raises(worker.BackupWorkerError,match='application_or_database_environment_refused'):execute(job)
    assert transport.calls==[] and {p.name for p in directory.iterdir()}=={'snapshot-export.tar','uploads.tar.gz'}


def test_same_job_competing_worker_is_rejected_before_any_output(job):
    directory,_,transport=job
    with worker.locked_job(directory):
        with pytest.raises(worker.BackupWorkerError,match='job_already_running'):execute(job)
    assert transport.calls==[]
    execute(job)


def test_low_disk_and_input_budget_fail_before_provider(job,monkeypatch):
    directory,_,transport=job
    with monkeypatch.context() as patch:
        patch.setattr(worker.shutil,'disk_usage',lambda path:SimpleNamespace(free=1))
        with pytest.raises(worker.BackupWorkerError,match='insufficient_free_space'):execute(job)
    with (directory/'snapshot-export.tar').open('r+b') as output:output.truncate(2*1024*1024)
    with pytest.raises(worker.BackupWorkerError,match='input_capacity_exceeded'):execute(job)
    assert transport.calls==[]


def test_verification_failure_removes_only_generated_files(job,monkeypatch):
    directory,_,_=job
    before={p.name:p.read_bytes() for p in directory.iterdir()}
    def refuse(*args,**kwargs):raise JointBackupError('synthetic_verification_failure')
    monkeypatch.setattr(worker,'verify_joint',refuse)
    with pytest.raises(JointBackupError):execute(job)
    assert {p.name:p.read_bytes() for p in directory.iterdir()}==before


@pytest.mark.parametrize('mode',['directory-public','file-public','symlink','hardlink','extra-file'])
def test_nonprivate_or_ambiguous_inputs_preserved(job,mode):
    directory,_,transport=job
    if mode=='directory-public':directory.chmod(0o755)
    elif mode=='file-public':(directory/'uploads.tar.gz').chmod(0o644)
    elif mode=='extra-file':(directory/'unexpected').touch()
    else:
        target=directory.parent/'saved-upload';(directory/'uploads.tar.gz').rename(target)
        if mode=='symlink':(directory/'uploads.tar.gz').symlink_to(target)
        else:os.link(target,directory/'uploads.tar.gz')
    before={p.name for p in directory.iterdir()}
    with pytest.raises(worker.BackupWorkerError):execute(job)
    assert {p.name for p in directory.iterdir()}==before and transport.calls==[]


def test_cli_help_and_import_need_no_application_or_database_environment():
    here=WORKER
    code='import sys; import backup_worker; assert not any(n=="app" or n.startswith(("app.","sqlalchemy","psycopg")) for n in sys.modules); print("independent")'
    env={'PATH':'/usr/bin:/bin','PYTHONPATH':str(CLOUD/'backend')}
    result=subprocess.run([sys.executable,'-c',code],cwd=here,env=env,capture_output=True,text=True,check=True)
    assert result.stdout.strip()=='independent'
    result=subprocess.run([sys.executable,str(here/'backup_worker.py'),'--help'],env=env,capture_output=True,text=True,check=True)
    assert '--maximum-bytes' in result.stdout and result.stderr==''


def test_missing_dedicated_credentials_refuses_only_the_requested_job(job,monkeypatch):
    directory,_,_=job
    for name in ['OSS_ACCESS_KEY_ID','OSS_ACCESS_KEY_SECRET','OSS_SESSION_TOKEN']:monkeypatch.delenv(name,raising=False)
    from formal_object_backup import ObjectBackupError
    with pytest.raises(ObjectBackupError,match='object_reader_unavailable'):
        worker.pack(directory,region='cn-hangzhou',bucket='synthetic-backup',maximum_bytes=1024*1024)
    assert {p.name for p in directory.iterdir()}=={'snapshot-export.tar','uploads.tar.gz'}


def test_compose_worker_isolated_from_application_and_database_configuration():
    import yaml
    here=Path(__file__).resolve().parent;config=yaml.safe_load((CLOUD/'docker-compose.yml').read_text())
    service=config['services']['object-backup']
    assert service['profiles']==['ops'] and service['restart']=='no'
    assert service['read_only'] is True and service['cap_drop']==['ALL']
    assert service['security_opt']==['no-new-privileges:true']
    assert set(service['environment'])=={'OSS_ACCESS_KEY_ID','OSS_ACCESS_KEY_SECRET','OSS_SESSION_TOKEN','PYTHONPATH'}
    assert all(':?' not in value for value in service['environment'].values())
    assert service['networks']==['backup_egress'] and config['networks']['backup_egress']=={}
    assert service['environment']['PYTHONPATH']=='/app'
    assert 'COPY formal_file_integrity.py ./formal_file_integrity.py' in (CLOUD/'backend/Dockerfile').read_text()
    assert service['volumes']==['./deployment/backup-worker:/opt/rsc-backup-worker:ro']
    assert not any(k in service for k in ['privileged','env_file','depends_on','ports','network_mode'])


def test_api_and_worker_import_the_same_integrity_definitions():
    from app.formal_services import file_storage
    for name in ['FILE_METADATA_SCHEMA','FileUploadIntentInput','FormalFileError','PURPOSES',
                 '_validate_file_row','_validate_intent_metadata','_validate_object_head',
                 '_head_manifest_sha256','_prepare_upload','_upload_request_hash']:
        assert getattr(production,name) is getattr(pure,name)
    assert file_storage.StoredObjectHead is pure.StoredObjectHead


@pytest.mark.parametrize('kind',['oversized','symlink','sparse','compressed','duplicate','truncated'])
def test_archive_stream_rejects_unsafe_headers_without_completion(tmp_path,kind):
    path=tmp_path/'input.tar';directory=tmp_path/'unpacked';directory.mkdir()
    if kind=='duplicate':
        import io
        with tarfile.open(path,'w') as archive:
            for _ in range(2):
                item=tarfile.TarInfo('one');item.size=1;archive.addfile(item,io.BytesIO(b'x'))
    elif kind=='compressed':path.write_bytes(gzip.compress(tar_bytes({'one':b'x'})))
    else:
        item=tarfile.TarInfo('one');item.size=2**40 if kind=='oversized' else 100
        if kind=='symlink':item.type=tarfile.SYMTYPE;item.linkname='../escape'
        if kind=='sparse':item.type=tarfile.GNUTYPE_SPARSE
        path.write_bytes(item.tobuf(format=tarfile.GNU_FORMAT if kind=='oversized' else tarfile.USTAR_FORMAT))
    if kind=='oversized':
        with pytest.raises(JointBackupError,match='invalid_archive_member'):
            unpack_exact(path,directory,{'one'},maximum_bytes=64*1024)
        assert not list(directory.iterdir())
        return
    with pytest.raises((JointBackupError,tarfile.TarError,ValueError)):
        unpack_exact(path,directory,{'one'},maximum_bytes=64*1024)
    assert not (tmp_path/'escape').exists()
    assert sum(p.stat().st_size for p in directory.iterdir())<=100
