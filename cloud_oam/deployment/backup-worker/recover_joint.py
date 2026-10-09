#!/usr/bin/env python3
"""Read one verified archive version into a new private directory; no restore.

Use the existing deadline_runner and a separately approved recovery identity.
An interrupted output directory is retained, never automatically replayed.
"""
import argparse
import contextlib
from hashlib import sha256
from importlib.metadata import version
import json
import logging
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

import archive_joint as shared


class RecoveryTransport(shared.ExactTransport):
    def __init__(self, inner, key, version_id):
        super().__init__(inner, key, True)
        self.version_id = version_id

    def send(self, request, **kwargs):
        shared.require(request.method in ('HEAD', 'GET')
            and parse_qs(urlsplit(request.url).query, keep_blank_values=True) == {'versionId': [self.version_id]})
        return super().send(request, **kwargs)


def client_for(value):
    import alibabacloud_oss_v2 as oss
    from backup_recovery_identity import credentials
    from formal_object_backup import sdk_client
    shared.require(version('alibabacloud-oss-v2') == '1.3.2' and version('alibabacloud-credentials') == '1.0.12')
    transport = RecoveryTransport(oss.transport.RequestsHttpClient(connect_timeout=3,
        readwrite_timeout=30, enabled_redirect=False, insecure_skip_verify=False), value['key'], value['version_id'])
    return sdk_client(oss.Config(region=shared.REGION, credentials_provider=credentials(), signature_version='v4',
        http_client=transport, enabled_redirect=False, insecure_skip_verify=False, disable_ssl=False,
        retry_max_attempts=1, connect_timeout=3, readwrite_timeout=30, additional_headers=['if-match', 'accept-encoding']))


def trusted_journal(directory, expected_sha256):
    """A controlled private copy plus the separately approved journal digest."""
    shared.require(type(expected_sha256) is str and re.fullmatch('[0-9a-f]{64}', expected_sha256))
    directory = Path(directory); shared.private_directory(directory)
    fd = os.open(directory / 'journal.json', os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        before = shared.regular(source.fileno()); shared.require(0 < before[2] <= 8192)
        data = source.read(8193)
        shared.require(sha256(data).hexdigest() == expected_sha256 and before == shared.regular(source.fileno()))
    value = shared.load(directory)
    shared.require(json.loads(data) == value and value['state'] == 'verified'
        and shared.valid_version(value['version_id']))
    return value


def download(journal_directory, journal_sha256, destination, maximum_bytes, *, client_factory=client_for, verifier=None):
    import alibabacloud_oss_v2 as oss
    from joint_backup import verify_joint
    verifier = verify_joint if verifier is None else verifier
    shared.deadline()
    shared.require(type(maximum_bytes) is int and 1024**2 <= maximum_bytes <= shared.MAXIMUM_BYTES
        and maximum_bytes % 1024 == 0)
    value = trusted_journal(journal_directory, journal_sha256)
    shared.require(value['size_bytes'] <= maximum_bytes)
    destination = Path(destination); shared.private_directory(destination.parent)
    shared.require(shared.shutil.disk_usage(destination.parent).free >= 8 * maximum_bytes)
    destination.mkdir(mode=0o700)  # exclusive: no retry over partial evidence
    shared.private_directory(destination)
    # An immutable request marker precedes cloud reads and survives a timeout.
    marker = dict(schema='rsc.backup-recovery-request.v1', journalSha256=journal_sha256,
        backupId=value['backup_id'], key=value['key'], versionId=value['version_id'])
    shared.save(destination, {**marker, 'state': 'read_started'})
    client = client_factory(value); shared.deadline()
    head = client.head_object(oss.HeadObjectRequest(bucket=shared.BUCKET, key=value['key'], version_id=value['version_id']))
    shared.matches(head, value)
    shared.deadline()
    response = client.get_object(oss.GetObjectRequest(bucket=shared.BUCKET, key=value['key'],
        version_id=value['version_id'], if_match=head.etag, accept_encoding='identity'))
    partial = destination/'download.partial'; complete = destination/'verified-joint.tar'
    try:
        shared.matches(response, value)
        shared.require(response.etag == head.etag and response.body is not None)
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            total, digest = 0, sha256()
            for chunk in response.body.iter_bytes(block_size=64 * 1024):
                shared.deadline(); shared.require(type(chunk) is bytes and 0 < len(chunk) <= 64 * 1024)
                total += len(chunk); shared.require(total <= value['size_bytes'])
                stream.write(chunk); digest.update(chunk)
            shared.require(total == value['size_bytes'] and digest.hexdigest() == value['sha256'])
            stream.flush(); os.fsync(stream.fileno()); shared.regular(stream.fileno())
    finally:
        if response.body is not None: response.body.close()
    # No trusted final filename until the independent existing package verifier
    # has accepted the downloaded bytes. This does not connect to any database.
    shared.deadline(); verifier(partial, destination/'verified-contents', maximum_bytes=maximum_bytes)
    os.link(partial, complete)  # atomic exclusive publication; never overwrite
    partial.unlink()
    result = dict(schema='rsc.backup-recovery-download.v1', backupId=value['backup_id'],
        key=value['key'], versionId=value['version_id'], sha256=value['sha256'], sizeBytes=value['size_bytes'],
        downloadedPackageVerified=True, databaseRestored=False, objectsRestored=False,
        releaseReady=False, autoRetry=False)
    shared.save(destination, {**marker, 'state': 'package_verified', **result})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--journal-directory', type=Path)
    parser.add_argument('--journal-sha256')
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--maximum-bytes', type=int)
    args = parser.parse_args()
    if not args.download: parser.print_help(); return 0
    if any(value is None for value in (args.journal_directory, args.journal_sha256, args.destination, args.maximum_bytes)):
        parser.error('private input, approved digest, new destination and capacity required')
    result = dict(schema='rsc.backup-recovery-download.v1', state='blocked_or_unknown',
        downloadedPackageVerified=False, databaseRestored=False, objectsRestored=False, releaseReady=False, autoRetry=False)
    logging.disable(logging.CRITICAL)
    with open(os.devnull, 'w') as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try: result = download(args.journal_directory, args.journal_sha256, args.destination, args.maximum_bytes)
        except Exception: pass
    print(json.dumps(result, sort_keys=True)); return 0 if result['downloadedPackageVerified'] else 1


if __name__ == '__main__': raise SystemExit(main())
