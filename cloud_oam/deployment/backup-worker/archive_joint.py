#!/usr/bin/env python3
"""Single-attempt remote archive of an already verified joint backup.

Run under the existing deadline_runner. A persisted put_started is never
replayed: --reconcile only HEADs/GETs its exact key. No cloud restore is implied.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
from hashlib import sha256
import io
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import stat
import time
import uuid
from urllib.parse import parse_qs, urlsplit

ACCOUNT = '1934673129483837'
REGION = 'cn-hangzhou'
BUCKET = 'rsc-pilot-backups-' + ACCOUNT
ROLE = 'acs:ram::' + ACCOUNT + ':role/rsc-pilot-backup-writer'
SCHEMA = 'rsc.joint-archive-attempt.v1'
MAXIMUM_BYTES = 1024**3  # Bounded pilot single-PUT path; no multipart expansion.
STATES = {'prepared', 'key_exists', 'preflight_unknown', 'put_started', 'unknown', 'verified'}


class ArchiveRefused(RuntimeError):
    pass


def require(value):
    if not value:
        raise ArchiveRefused('archive_boundary_refused')


def deadline():
    remaining = float(os.environ.get('RSC_BACKUP_DEADLINE_MONOTONIC', 'nan')) - time.monotonic()
    require(math.isfinite(remaining) and 0 < remaining <= 86400)


def writer_credentials(environment):
    from pilot_preflight import PNVS_STATIC_FIELDS
    from backup_reader_identity import GuardedBackupCredentials, projection_metadata
    static = (*PNVS_STATIC_FIELDS, 'RSC_OSS_BACKUP_ACCESS_KEY_ID', 'RSC_OSS_BACKUP_ACCESS_KEY_SECRET', 'RSC_OSS_BACKUP_SESSION_TOKEN')
    require(not any(environment.get(key) for key in static)
        and not any(key.startswith(('OAM_', 'PG', 'POSTGRES_')) for key in environment))
    values = {name: environment.get('RSC_BACKUP_WRITER_' + name, '') for name in
        ('ACCOUNT_ID', 'ROLE_ARN', 'PROVIDER_ARN', 'SESSION_NAME', 'TOKEN_FILE', 'WRITER',
         'PROJECTOR_UID', 'SHARED_GID', 'WORKER_UID', 'WORKER_GID')}
    require(values['ACCOUNT_ID'] == ACCOUNT and values['ROLE_ARN'] == ROLE
        and re.fullmatch(r'acs:ram::' + ACCOUNT + r':oidc-provider/[A-Za-z0-9._-]{1,128}', values['PROVIDER_ARN'])
        and re.fullmatch(r'[A-Za-z0-9.@_-]{2,64}', values['SESSION_NAME'])
        and values['TOKEN_FILE'] == '/run/rsc-backup-writer/oidc.jwt'
        and values['WRITER'] == 'openbao_agent_template_v1'
        and [values[k] for k in ('PROJECTOR_UID', 'SHARED_GID', 'WORKER_UID', 'WORKER_GID')]
            == ['23208', '23218', '23209', '23219'])
    identity = dict(token=values['TOKEN_FILE'], owner=23208, shared=23218, uid=23209, gid=23219)
    initial = projection_metadata(identity)
    from app.oss_runtime_credentials import OssOidcIdentity, OssOidcCredentialsProvider
    provider = OssOidcCredentialsProvider(OssOidcIdentity(role_arn=ROLE,
        provider_arn=values['PROVIDER_ARN'], token_file=identity['token'], region=REGION,
        session_name=values['SESSION_NAME']))
    return GuardedBackupCredentials(provider, identity, initial)


class ExactTransport:
    def __init__(self, inner, key, reconcile):
        self.inner, self.key = inner, key
        self.remaining = {'PUT': 0 if reconcile else 1, 'HEAD': 1 if reconcile else 2, 'GET': 1}

    def open(self): return self.inner.open()
    def close(self): return self.inner.close()

    def send(self, request, **kwargs):
        deadline()
        url = urlsplit(request.url)
        query = parse_qs(url.query, keep_blank_values=True)
        require(url.scheme == 'https' and url.hostname == BUCKET + '.oss-' + REGION + '.aliyuncs.com'
            and url.port in (None, 443) and not url.username and not url.password and not url.fragment
            and url.path == '/' + self.key and request.method in self.remaining
            and self.remaining[request.method] > 0
            and (not query or request.method in ('HEAD', 'GET') and set(query) == {'versionId'}
                 and len(query['versionId']) == 1 and valid_version(query['versionId'][0])))
        self.remaining[request.method] -= 1
        return self.inner.send(request, **kwargs)


def sdk_client(config):
    """Bind conditional read headers through the pinned SDK signer hook."""
    import alibabacloud_oss_v2 as oss
    from alibabacloud_oss_v2.signer import SignerV4

    class BoundArchiveSigner(SignerV4):
        def sign(self, context):
            require(context.request.method in {'HEAD', 'GET', 'PUT'})
            context.additional_headers = set(context.additional_headers or ()) | {'if-match', 'accept-encoding'}
            super().sign(context)

    return oss.Client(config, signer=BoundArchiveSigner())


def make_client(key, reconcile):
    import alibabacloud_oss_v2 as oss
    from importlib.metadata import version
    require(version('alibabacloud-oss-v2') == '1.3.2'
            and version('alibabacloud-credentials') == '1.0.12')
    credentials = writer_credentials(os.environ)
    transport = ExactTransport(oss.transport.RequestsHttpClient(connect_timeout=3,
        readwrite_timeout=30, enabled_redirect=False, insecure_skip_verify=False), key, reconcile)
    config = oss.Config(region=REGION, credentials_provider=credentials, signature_version='v4',
        http_client=transport, disable_ssl=False, insecure_skip_verify=False,
        enabled_redirect=False, retry_max_attempts=1, connect_timeout=3, readwrite_timeout=30,
        additional_headers=['if-match', 'accept-encoding'])
    return sdk_client(config)


def valid_version(value):
    return type(value) is str and value != 'null' and re.fullmatch(r'[A-Za-z0-9+/=_-]{1,1024}', value) is not None


def private_directory(path):
    require(path.is_absolute() and path == path.resolve() and not path.is_symlink())
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o700 and os.geteuid() != 0)


def regular(fd):
    value = os.fstat(fd)
    require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1
            and value.st_uid == os.geteuid() and stat.S_IMODE(value.st_mode) == 0o600)
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def save(attempt, value):
    private_directory(attempt)
    temporary = attempt / 'journal.new'
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(value, out, sort_keys=True); out.write('\n'); out.flush(); os.fsync(out.fileno())
    os.replace(temporary, attempt / 'journal.json')
    parent = os.open(attempt, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(parent)
    finally: os.close(parent)


def load(attempt):
    private_directory(attempt)
    fd = os.open(attempt / 'journal.json', os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r') as stream:
        require(regular(stream.fileno())[2] <= 8192)
        value = json.load(stream)
    require(type(value) is dict and set(value) == {'schema', 'backup_id', 'key', 'bucket', 'region',
        'sha256', 'size_bytes', 'state', 'version_id'} and value['schema'] == SCHEMA
        and type(value['backup_id']) is str and re.fullmatch(r'[0-9a-f]{32}', value['backup_id'])
        and value['key'] == 'joint/v1/' + value['backup_id'] + '/verified-joint.tar'
        and value['bucket'] == BUCKET and value['region'] == REGION
        and type(value['sha256']) is str and re.fullmatch(r'[0-9a-f]{64}', value['sha256'])
        and type(value['size_bytes']) is int and 0 < value['size_bytes'] <= MAXIMUM_BYTES
        and value['state'] in STATES and (value['version_id'] is None or valid_version(value['version_id'])))
    return value


class BoundedBody(io.IOBase):
    def __init__(self, stream, size): self.stream, self.size = stream, size
    def __len__(self): return self.size
    def tell(self): return self.stream.tell()
    def read(self, size=-1):
        deadline()
        left = self.size - self.tell()
        require(left >= 0)
        return self.stream.read(min(left, size if size >= 0 else left, 64 * 1024))
    def seek(self, offset, whence=0):
        target = offset if whence == 0 else self.tell() + offset if whence == 1 else self.size + offset if whence == 2 else -1
        require(0 <= target <= self.size)
        return self.stream.seek(target)
    def readable(self): return True
    def seekable(self): return True


def matches(result, value):
    require(type(result.status_code) is int and result.status_code == 200
        and type(result.content_length) is int and result.content_length == value['size_bytes']
        and result.server_side_encryption == 'AES256' and valid_version(result.version_id)
        and result.metadata == {'sha256': value['sha256'], 'backup-id': value['backup_id']})
    headers = {str(key).lower(): val for key, val in result.headers.items()}
    require('content-range' not in headers and 'x-oss-delete-marker' not in headers
        and headers.get('content-encoding', 'identity') == 'identity'
        and headers.get('content-type') == 'application/x-tar')
    require(type(result.etag) is str and re.fullmatch(r'"[^"\x00-\x20\x7f]+"', result.etag))
    if value['version_id'] is not None:
        require(result.version_id == value['version_id'])


def verify_remote(client, value):
    import alibabacloud_oss_v2 as oss
    deadline()
    head = client.head_object(oss.HeadObjectRequest(bucket=BUCKET, key=value['key'], version_id=value['version_id']))
    matches(head, value)
    observed_version = head.version_id
    deadline()
    response = client.get_object(oss.GetObjectRequest(bucket=BUCKET, key=value['key'],
        version_id=observed_version, if_match=head.etag, accept_encoding='identity'))
    try:
        matches(response, {**value, 'version_id': observed_version})
        require(response.etag == head.etag and response.body is not None)
        length, digest = 0, sha256()
        for chunk in response.body.iter_bytes(block_size=64 * 1024):
            deadline(); require(type(chunk) is bytes and 0 < len(chunk) <= 64 * 1024)
            length += len(chunk); require(length <= value['size_bytes']); digest.update(chunk)
        require(length == value['size_bytes'] and digest.hexdigest() == value['sha256'])
    finally:
        if response.body is not None: response.body.close()
    return observed_version


def report(value):
    return dict(schema=SCHEMA, state=value['state'], backupId=value['backup_id'],
        key=value['key'], sha256=value['sha256'], sizeBytes=value['size_bytes'],
        versionId=value['version_id'], remoteArchiveVerified=value['state'] == 'verified',
        restoreVerified=False, releaseReady=False, autoRetry=False)


def archive(bundle, attempt, maximum_bytes, *, reconcile=False, client_factory=make_client, verifier=None):
    from joint_backup import verify_joint
    import alibabacloud_oss_v2 as oss
    verifier = verify_joint if verifier is None else verifier
    require(type(maximum_bytes) is int and 1024**2 <= maximum_bytes <= MAXIMUM_BYTES and maximum_bytes % 1024 == 0)
    attempt = Path(attempt); private_directory(attempt.parent); deadline()
    if not reconcile: attempt.mkdir(mode=0o700)  # existing/unknown attempt cannot be replayed
    private_directory(attempt)
    directory = os.open(attempt, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    stream = None
    try:
        fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if reconcile:
            value = load(attempt)
            require(value['size_bytes'] <= maximum_bytes and value['state'] in ('put_started', 'unknown', 'verified'))
        else:
            bundle = Path(bundle)
            require(bundle.name == 'verified-joint.tar' and bundle.is_absolute() and bundle == bundle.resolve())
            stream = os.fdopen(os.open(bundle, os.O_RDONLY | os.O_NOFOLLOW), 'rb')
            before = regular(stream.fileno()); require(0 < before[2] <= maximum_bytes)
            require(shutil.disk_usage(attempt).free >= 8 * maximum_bytes)
            verify_path = attempt / 'verified'
            try: verifier(bundle, verify_path, maximum_bytes=maximum_bytes)
            finally:
                if verify_path.is_dir() and not verify_path.is_symlink(): shutil.rmtree(verify_path)
            require(before == regular(stream.fileno()) and (bundle.stat().st_dev, bundle.stat().st_ino) == before[:2])
            digest = sha256()
            bounded = BoundedBody(stream, before[2])
            while chunk := bounded.read(64 * 1024): digest.update(chunk)
            require(stream.tell() == before[2] and before == regular(stream.fileno())); stream.seek(0)
            backup_id = uuid.uuid4().hex
            value = dict(schema=SCHEMA, backup_id=backup_id,
                key='joint/v1/' + backup_id + '/verified-joint.tar', bucket=BUCKET, region=REGION,
                sha256=digest.hexdigest(), size_bytes=before[2], state='prepared', version_id=None)
            save(attempt, value)
        client = client_factory(value['key'], reconcile)
        if not reconcile:
            try:
                result = client.head_object(oss.HeadObjectRequest(bucket=BUCKET, key=value['key']))
                value['state'] = 'key_exists' if result.status_code == 200 else 'preflight_unknown'
                save(attempt, value); return report(value)
            except Exception as error:
                # Locked OSS 1.3.2 exposes service failure through this public
                # one-level wrapper. No raw body/message/cause is emitted.
                service = error.unwrap() if type(error) is oss.exceptions.OperationError else error
                if (type(service) is not oss.exceptions.ServiceError
                        or service.status_code != 404 or service.code != 'NoSuchKey'):
                    value['state'] = 'preflight_unknown'; save(attempt, value); return report(value)
            deadline(); require(before == regular(stream.fileno()))
            value['state'] = 'put_started'; save(attempt, value)
            try:
                result = client.put_object(oss.PutObjectRequest(bucket=BUCKET, key=value['key'],
                    body=BoundedBody(stream, value['size_bytes']), content_length=value['size_bytes'],
                    content_type='application/x-tar', server_side_encryption='AES256',
                    metadata={'sha256': value['sha256'], 'backup-id': value['backup_id']}))
                require(result.status_code == 200 and valid_version(result.version_id))
                value['version_id'] = result.version_id; save(attempt, value)
            except Exception:
                value['state'] = 'unknown'; save(attempt, value); return report(value)
        try:
            value['version_id'] = verify_remote(client, value)
            value['state'] = 'verified'
        except Exception:
            value['state'] = 'unknown'
        save(attempt, value)
        return report(value)
    finally:
        if stream is not None: stream.close()
        os.close(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', action='store_true')
    parser.add_argument('--reconcile', action='store_true')
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--attempt-dir', type=Path)
    parser.add_argument('--maximum-bytes', type=int)
    args = parser.parse_args()
    if not args.archive and not args.reconcile: parser.print_help(); return 0
    if args.archive == args.reconcile or args.attempt_dir is None or args.maximum_bytes is None or (args.archive and args.bundle is None):
        parser.error('select one mode, a private attempt directory and a capacity limit')
    logging.disable(logging.CRITICAL)
    result = dict(schema=SCHEMA, state='blocked_or_unknown', remoteArchiveVerified=False,
                  restoreVerified=False, releaseReady=False, autoRetry=False)
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try: result = archive(args.bundle, args.attempt_dir, args.maximum_bytes, reconcile=args.reconcile)
        except Exception: pass
    print(json.dumps(result, sort_keys=True))
    return 0 if result['remoteArchiveVerified'] else 1


if __name__ == '__main__': raise SystemExit(main())
