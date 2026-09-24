from backup_test_support import CLOUD, WORKER, OPS
from pathlib import Path
from types import SimpleNamespace
import json,os,resource,subprocess,sys
import pytest
import backup_worker as worker

HERE=Path(__file__).resolve().parent
HELPER=OPS/'capacity.sh'


def shell(tmp_path,code,*,budget='1048576',path='/usr/bin:/bin'):
    return subprocess.run(['/bin/sh','-c',code,'capacity-test',str(HELPER),str(tmp_path),sys.executable],
        env={'PATH':path,'RSC_BACKUP_MAXIMUM_BYTES':budget},capture_output=True,text=True,timeout=15)


@pytest.mark.parametrize('budget',['','0','01','-1','1e6','1048575','1048577','1099511628800','9'*50])
def test_invalid_budget_refused_before_any_probe(tmp_path,budget):
    result=shell(tmp_path,'. "$1"; capacity_prepare "$2" 8',budget=budget)
    assert result.returncode!=0 and list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('extra',[0,1])
def test_measured_shell_cap_is_exact_bytes_and_parent_unchanged(tmp_path,extra):
    before=resource.getrlimit(resource.RLIMIT_FSIZE)
    writer=tmp_path/'writer.py'
    writer.write_text('import os,resource,json\nprint(json.dumps(resource.getrlimit(resource.RLIMIT_FSIZE)),file=__import__("sys").stderr)\n'
        'data=b"x"*'+str(1048576+extra)+'\nwhile data:\n n=os.write(1,data);data=data[n:]\n')
    result=shell(tmp_path,'. "$1"; capacity_prepare "$2" 8; capacity_run "$3" "$2/writer.py" > "$2/output"')
    assert (result.returncode==0)==(extra==0)
    assert json.loads(result.stderr.splitlines()[0])==[1048576,1048576]
    assert (tmp_path/'output').stat().st_size==1048576
    assert not list(tmp_path.glob('.rsc-capacity.*'))
    assert resource.getrlimit(resource.RLIMIT_FSIZE)==before


def test_low_disk_precedes_probe_and_child(tmp_path):
    binary=tmp_path/'bin';binary.mkdir()
    df=binary/'df';df.write_text('#!/bin/sh\nprintf "Filesystem 1024-blocks Used Available Capacity Mounted on\\nfixture 20 19 1 95%% /fixture\\n"\n');df.chmod(0o700)
    result=shell(tmp_path,'. "$1"; capacity_prepare "$2" 8 || exit $?; touch "$2/child-ran"',path=str(binary)+':/usr/bin:/bin')
    assert result.returncode!=0 and 'RSC_BACKUP_DISK_INSUFFICIENT' in result.stderr
    assert not (tmp_path/'child-ran').exists() and not list(tmp_path.glob('.rsc-capacity.*'))


@pytest.fixture
def empty_job(tmp_path,monkeypatch):
    for name in list(os.environ):
        if name.startswith(('OAM_','PG','POSTGRES_')):monkeypatch.delenv(name)
    directory=tmp_path/'job';directory.mkdir(mode=0o700)
    return directory


def test_worker_preflight_leaves_empty_job_and_performs_no_read(empty_job):
    calls=[]
    result=worker.preflight(empty_job,region='cn-hangzhou',bucket='synthetic-backup',maximum_bytes=1048576,
        reader_factory=lambda **kw:calls.append(kw))
    assert result['status']=='preflight_passed' and result['cloud_authorization_verified'] is False
    assert result['disk_space_reserved'] is False and len(calls)==1 and list(empty_job.iterdir())==[]


def test_worker_preflight_low_disk_before_credentials(empty_job,monkeypatch):
    monkeypatch.setattr(worker.shutil,'disk_usage',lambda p:SimpleNamespace(free=8388607))
    def forbidden(**kw):raise AssertionError('must not initialize reader')
    with pytest.raises(worker.BackupWorkerError,match='insufficient_free_space'):
        worker.preflight(empty_job,region='cn-hangzhou',bucket='synthetic-backup',maximum_bytes=1048576,reader_factory=forbidden)
    assert list(empty_job.iterdir())==[]


@pytest.mark.parametrize('budget',[1048575,1048577,1099511628800,True])
def test_worker_capacity_matches_shell(empty_job,budget):
    with pytest.raises(worker.BackupWorkerError,match='capacity_limit_invalid'):
        worker.preflight(empty_job,region='cn-hangzhou',bucket='synthetic-backup',maximum_bytes=budget)


def test_preflight_missing_credentials_precedes_export(empty_job,monkeypatch):
    for name in ['OSS_ACCESS_KEY_ID','OSS_ACCESS_KEY_SECRET','OSS_SESSION_TOKEN']:monkeypatch.delenv(name,raising=False)
    with pytest.raises(worker.ObjectBackupError,match='object_reader_unavailable'):
        worker.preflight(empty_job,region='cn-hangzhou',bucket='synthetic-backup',maximum_bytes=1048576)
    assert list(empty_job.iterdir())==[]


def test_preflight_with_synthetic_sdk_credentials_makes_no_network_call(empty_job,monkeypatch):
    import socket
    monkeypatch.setenv('OSS_ACCESS_KEY_ID','SYNTHETIC_TEST_ID')
    monkeypatch.setenv('OSS_ACCESS_KEY_SECRET','synthetic-not-a-live-secret')
    def forbidden(*args,**kwargs):raise AssertionError('preflight must not contact storage')
    monkeypatch.setattr(socket,'create_connection',forbidden)
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    result=worker.preflight(empty_job,region='cn-hangzhou',bucket='synthetic-backup',maximum_bytes=1048576)
    assert result['status']=='preflight_passed' and list(empty_job.iterdir())==[]


@pytest.mark.parametrize('oversized',[False,True])
def test_actual_legacy_tar_output_is_bounded_and_staging_cleaned(tmp_path,oversized):
    import io,tarfile
    binary=tmp_path/'bin';binary.mkdir();temp=tmp_path/'tmp';temp.mkdir()
    uploads=tmp_path/'uploads';uploads.mkdir()
    payload=os.urandom(2*1048576) if oversized else b'synthetic legacy attachment'
    (uploads/'file.bin').write_bytes(payload)
    adapter=binary/'tar'
    # Only map the fixed container path to this test-owned fixture. Execute the
    # real tar/compressor with the actual inherited file limit.
    adapter.write_text('#!'+sys.executable+'\nimport os,sys\nargs=sys.argv[1:]\n'
        'assert args[:1]==["-czf"] and args[2:]==["-C","/data/uploads","."]\n'
        'args[3]='+repr(str(uploads))+'\nos.execv("/usr/bin/tar",["tar",*args])\n')
    adapter.chmod(0o700)
    result=subprocess.run(['/bin/sh',str(OPS/'legacy-export.sh')],
        env={'PATH':str(binary)+':/usr/bin:/bin','TMPDIR':str(temp),'RSC_BACKUP_MAXIMUM_BYTES':'1048576'},
        capture_output=True,timeout=15)
    assert (result.returncode!=0)==oversized and list(temp.iterdir())==[]
    assert (uploads/'file.bin').read_bytes()==payload
    if oversized:assert result.stdout==b''
    else:
        with tarfile.open(fileobj=io.BytesIO(result.stdout),mode='r:gz') as archive:
            assert archive.extractfile('./file.bin').read()==payload


@pytest.mark.parametrize('stage',['catalog','dump','legacy'])
def test_producer_overflow_publishes_nothing_and_cleans_staging(tmp_path,stage):
    binary=tmp_path/'bin';binary.mkdir();temp=tmp_path/'tmp';temp.mkdir()
    for name in ['psql','pg_dump','tar']:
        script=binary/name
        script.write_text('#!'+sys.executable+'\nimport os,sys\nfrom pathlib import Path\n'
            'stage='+repr(stage)+'\nname='+repr(name)+'\n'
            'if name=="tar":\n out=open(sys.argv[2],"wb");out.write(b"x"*1048577);out.close()\n'
            'else:\n data=b"x"*(1048577 if (stage=="catalog" and name=="psql") or (stage=="dump" and name=="pg_dump") else 10)\n while data:\n  n=os.write(1,data);data=data[n:]\n')
        script.chmod(0o700)
    env={'PATH':str(binary)+':/usr/bin:/bin','TMPDIR':str(temp),'RSC_BACKUP_MAXIMUM_BYTES':'1048576',
        'RSC_BACKUP_LIB':str(OPS),'RSC_BACKUP_JOB':'synthetic_capacity',
        'RSC_BACKUP_SNAPSHOT':'00000001-00000001-1','PGDATABASE':'synthetic_capacity'}
    script=OPS/('legacy-export.sh' if stage=='legacy' else 'dump.sh')
    result=subprocess.run(['/bin/sh',str(script)],env=env,capture_output=True,timeout=15)
    assert result.returncode!=0 and result.stdout==b'' and list(temp.iterdir())==[]
