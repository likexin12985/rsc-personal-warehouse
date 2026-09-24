"""Dedicated OSS backup worker candidate: no database or application credentials."""
import argparse
from contextlib import contextmanager
import fcntl,json,os,shutil,stat,sys
from pathlib import Path

from formal_object_backup import OssBackupReader,ObjectBackupError
from joint_backup import JointBackupError,publish_joint,verify_joint,digest


class BackupWorkerError(RuntimeError):pass


@contextmanager
def locked_job(path):
    try:fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    except OSError:raise BackupWorkerError('job_directory_invalid') from None
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise BackupWorkerError('job_already_running') from None
        yield
    finally:os.close(fd)


def checked_job(path,expected):
    path=Path(path)
    if path.is_symlink() or not path.is_dir():raise BackupWorkerError('job_directory_invalid')
    metadata=path.stat()
    if metadata.st_uid!=os.geteuid() or stat.S_IMODE(metadata.st_mode)!=0o700:
        raise BackupWorkerError('job_directory_not_private')
    if {p.name for p in path.iterdir()}!=set(expected):raise BackupWorkerError('job_inputs_not_exact')
    for name in expected:
        p=path/name;s=p.lstat()
        if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_uid!=os.geteuid() or stat.S_IMODE(s.st_mode)&0o077:
            raise BackupWorkerError('job_input_not_private_regular_file')
    return path


def validate_environment(maximum_bytes):
    if type(maximum_bytes) is not int or not 1024*1024<=maximum_bytes<=1024**4 or maximum_bytes % 1024:
        raise BackupWorkerError('capacity_limit_invalid')
    if any(key.startswith(('OAM_','PG','POSTGRES_')) for key in os.environ):
        raise BackupWorkerError('application_or_database_environment_refused')


def preflight(job,*,region,bucket,maximum_bytes,reader_factory=OssBackupReader.from_environment):
    validate_environment(maximum_bytes)
    with locked_job(job):
        job=checked_job(job,set())
        if shutil.disk_usage(job).free<8*maximum_bytes:
            raise BackupWorkerError('insufficient_free_space')
        reader_factory(region=region,bucket=bucket)
        return dict(status='preflight_passed',maximum_bytes=maximum_bytes,required_free_bytes=8*maximum_bytes,
            disk_space_reserved=False,cloud_authorization_verified=False)


def pack(job,*,region,bucket,maximum_bytes,reader_factory=OssBackupReader.from_environment):
    validate_environment(maximum_bytes)
    with locked_job(job):
        return _pack_locked(job,region=region,bucket=bucket,maximum_bytes=maximum_bytes,reader_factory=reader_factory)


def _pack_locked(job,*,region,bucket,maximum_bytes,reader_factory):
    job=checked_job(job,{'snapshot-export.tar','uploads.tar.gz'})
    if any((job/name).stat().st_size>maximum_bytes for name in ['snapshot-export.tar','uploads.tar.gz']):
        raise BackupWorkerError('input_capacity_exceeded')
    # Source, staging, verified/decompressed copies and final tar coexist.
    if shutil.disk_usage(job).free<8*maximum_bytes:
        raise BackupWorkerError('insufficient_free_space')
    output=job/'verified-joint.tar';verification=job/'verified-contents'
    generated=[output,job/'verified-joint.sha256',job/'backup-complete.json']
    try:
        reader=reader_factory(region=region,bucket=bucket)
        manifest=publish_joint(job/'snapshot-export.tar',job/'uploads.tar.gz',reader,output,maximum_bytes=maximum_bytes)
        if verify_joint(output,verification,maximum_bytes=maximum_bytes)!=manifest:
            raise BackupWorkerError('verified_manifest_changed')
        checksum=digest(output);size=output.stat().st_size
        receipt=dict(schema='cloud_oam.backup_worker_candidate.v1',status='verified',bundle='verified-joint.tar',
            sha256=checksum,size_bytes=size,formal_object_count=manifest['formal_object_count'],
            pending_intent_count=manifest['pending_intent_count'],database_catalog_bound=True,cloud_restore_verified=False)
        for path,data in [(generated[1],f'{checksum}  verified-joint.tar\n'.encode()),
            (generated[2],(json.dumps(receipt,sort_keys=True)+'\n').encode())]:
            with path.open('xb') as stream:
                os.chmod(path,0o600);stream.write(data);stream.flush();os.fsync(stream.fileno())
        return receipt
    except BaseException:
        for path in generated:
            if path.is_file() and not path.is_symlink():path.unlink()
        raise
    finally:
        if verification.is_dir():shutil.rmtree(verification)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Create a verified joint backup from successful private export inputs.')
    parser.add_argument('--preflight',action='store_true')
    parser.add_argument('--job-dir',required=True,type=Path)
    parser.add_argument('--maximum-bytes',required=True,type=int)
    parser.add_argument('--region',required=True)
    parser.add_argument('--bucket',required=True)
    args=parser.parse_args(argv)
    try:
        receipt=(preflight if args.preflight else pack)(args.job_dir,region=args.region,bucket=args.bucket,maximum_bytes=args.maximum_bytes)
    except (ObjectBackupError,JointBackupError,BackupWorkerError) as error:
        print(json.dumps(dict(status='failed',code=str(error))),file=sys.stderr);return 1
    except Exception:
        print(json.dumps(dict(status='failed',code='backup_worker_failed')),file=sys.stderr);return 1
    print(json.dumps(receipt,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
