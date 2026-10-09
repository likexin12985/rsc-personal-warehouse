"""Dedicated pilot source-reader identity; no static/default credential chain.

Imports are cold until a backup is explicitly requested. The application OIDC
adapter is pure credential code; no Settings/database/application bootstrap is
loaded. The independently mounted release guard checks metadata only.
"""
from __future__ import annotations

import os
from pathlib import PurePosixPath
import re


class BackupIdentityUnavailable(RuntimeError):
    pass


def coordinates(environment, *, region, bucket):
    from pilot_preflight import PNVS_STATIC_FIELDS
    account = environment.get('RSC_BACKUP_READER_ACCOUNT_ID', '')
    role = environment.get('RSC_BACKUP_READER_ROLE_ARN', '')
    provider = environment.get('RSC_BACKUP_READER_PROVIDER_ARN', '')
    session = environment.get('RSC_BACKUP_READER_SESSION_NAME', '')
    token = environment.get('RSC_BACKUP_READER_TOKEN_FILE', '')
    source = environment.get('RSC_BACKUP_READER_SOURCE_BUCKET', '')
    values = [environment.get('RSC_BACKUP_READER_' + name, '') for name in
              ('PROJECTOR_UID', 'SHARED_GID', 'WORKER_UID', 'WORKER_GID')]
    static_fields = (*PNVS_STATIC_FIELDS, 'RSC_OSS_BACKUP_ACCESS_KEY_ID',
                     'RSC_OSS_BACKUP_ACCESS_KEY_SECRET', 'RSC_OSS_BACKUP_SESSION_TOKEN')
    if (any(environment.get(key) for key in static_fields)
            or any(key.startswith(('OAM_', 'PG', 'POSTGRES_')) for key in environment)
            or re.fullmatch(r'[0-9]{16}', account) is None
            or role != 'acs:ram::' + account + ':role/rsc-pilot-backup-reader'
            or re.fullmatch(r'acs:ram::' + account + r':oidc-provider/[A-Za-z0-9._-]{1,128}', provider) is None
            or re.fullmatch(r'[A-Za-z0-9.@_-]{2,64}', session) is None
            or region != 'cn-hangzhou' or source != 'rsc-pilot-attachments-' + account or bucket != source
            or environment.get('RSC_BACKUP_READER_PREFIX') != 'formal-files/v1/'
            or token != '/run/rsc-backup-reader/oidc.jwt'
            or environment.get('RSC_BACKUP_READER_WRITER') != 'openbao_agent_template_v1'
            or any(re.fullmatch(r'[1-9][0-9]{0,9}', value) is None or int(value) > 2_147_483_647 for value in values)):
        raise BackupIdentityUnavailable('backup_reader_identity_unavailable')
    owner, shared, uid, gid = map(int, values)
    if owner == uid or shared == gid:
        raise BackupIdentityUnavailable('backup_reader_identity_unavailable')
    return dict(role=role, provider=provider, session=session, token=token,
                owner=owner, shared=shared, uid=uid, gid=gid, region=region)


def projection_metadata(identity):
    from pilot_release import live_directory_metadata, linux_tmpfs_mount
    if (os.geteuid() != identity['uid'] or os.getegid() != identity['gid']
            or identity['shared'] not in os.getgroups()):
        raise BackupIdentityUnavailable('backup_reader_identity_unavailable')
    directory = PurePosixPath(identity['token']).parent
    row = dict(source=str(directory), leaf='oidc.jwt', kind='oss_oidc',
               owner=identity['owner'], group=identity['shared'], writer='openbao_agent_template_v1')
    value = live_directory_metadata(row)
    mount = linux_tmpfs_mount(directory)
    if value['mount'] != mount or 'ro' not in mount[5].split(',') or mount[4] != str(directory):
        raise BackupIdentityUnavailable('backup_reader_identity_unavailable')
    return value


class GuardedBackupCredentials:
    def __init__(self, provider, identity, initial):
        self.provider, self.identity, self.initial = provider, identity, initial

    def get_credentials(self):
        value = None
        try:
            if projection_metadata(self.identity) != self.initial:
                raise BackupIdentityUnavailable('backup_reader_identity_unavailable')
            candidate = self.provider.get_credentials()
            if projection_metadata(self.identity) != self.initial:
                raise BackupIdentityUnavailable('backup_reader_identity_unavailable')
            value = candidate
        except Exception:
            pass
        if value is None:
            raise BackupIdentityUnavailable('backup_reader_identity_unavailable')
        return value


def credentials_from_environment(*, region, bucket):
    value = None
    try:
        identity = coordinates(os.environ, region=region, bucket=bucket)
        initial = projection_metadata(identity)
        from app.oss_runtime_credentials import OssOidcCredentialsProvider, OssOidcIdentity
        provider = OssOidcCredentialsProvider(OssOidcIdentity(role_arn=identity['role'],
            provider_arn=identity['provider'], token_file=identity['token'],
            region=region, session_name=identity['session']))
        value = GuardedBackupCredentials(provider, identity, initial)
    except Exception:
        pass
    if value is None:
        raise BackupIdentityUnavailable('backup_reader_identity_unavailable')
    return value
