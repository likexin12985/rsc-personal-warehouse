"""Explicit, read-only backup recovery identity; never the archive writer."""
import os
import re


def credentials(environment=None):
    from archive_joint import ACCOUNT, REGION, require
    from backup_reader_identity import GuardedBackupCredentials, projection_metadata
    from pilot_preflight import PNVS_STATIC_FIELDS
    environment = os.environ if environment is None else environment
    values = {key: environment.get('RSC_BACKUP_RECOVERY_' + key, '') for key in
              ('ACCOUNT_ID', 'ROLE_ARN', 'PROVIDER_ARN', 'SESSION_NAME', 'TOKEN_FILE',
               'WRITER', 'PROJECTOR_UID', 'SHARED_GID', 'WORKER_UID', 'WORKER_GID')}
    require(not any(environment.get(key) for key in (*PNVS_STATIC_FIELDS,
        'RSC_OSS_BACKUP_ACCESS_KEY_ID', 'RSC_OSS_BACKUP_ACCESS_KEY_SECRET', 'RSC_OSS_BACKUP_SESSION_TOKEN'))
        and not any(key.startswith(('PG', 'OAM_', 'POSTGRES_')) for key in environment)
        and values['ACCOUNT_ID'] == ACCOUNT
        and values['ROLE_ARN'] == 'acs:ram::' + ACCOUNT + ':role/rsc-pilot-backup-recovery'
        and re.fullmatch(r'acs:ram::' + ACCOUNT + r':oidc-provider/[A-Za-z0-9._-]{1,128}', values['PROVIDER_ARN'])
        and re.fullmatch(r'[A-Za-z0-9.@_-]{2,64}', values['SESSION_NAME'])
        and values['TOKEN_FILE'] == '/run/rsc-backup-recovery/oidc.jwt'
        and values['WRITER'] == 'openbao_agent_template_v1'
        and [values[k] for k in ('PROJECTOR_UID', 'SHARED_GID', 'WORKER_UID', 'WORKER_GID')]
            == ['23210', '23220', '23211', '23221'])
    identity = dict(token=values['TOKEN_FILE'], owner=23210, shared=23220, uid=23211, gid=23221)
    initial = projection_metadata(identity)
    from app.oss_runtime_credentials import OssOidcCredentialsProvider, OssOidcIdentity
    provider = OssOidcCredentialsProvider(OssOidcIdentity(role_arn=values['ROLE_ARN'],
        provider_arn=values['PROVIDER_ARN'], session_name=values['SESSION_NAME'],
        token_file=identity['token'], region=REGION))
    return GuardedBackupCredentials(provider, identity, initial)
