"""Shared explicit OSS composition for API and independently spawned workers.

Construction performs no network or token read. The complete non-secret
identity is part of the cache key; actual credentials refresh in the provider.
"""
from functools import lru_cache

from .config import Settings
from .formal_services.file_storage import AliyunOssV2StorageAdapter, FileStorageError
from .oss_runtime_credentials import (
    OssOidcCredentialsProvider, OssOidcIdentity, OssRuntimeCredentialUnavailable,
)


@lru_cache(maxsize=8)
def _storage_adapter(provider, region, bucket, mode, role_arn, provider_arn,
                     token_file, session_name):
    if provider != "aliyun_oss_v2":
        return None
    value = None
    try:
        if mode == "oidc_role_arn":
            credentials = OssOidcCredentialsProvider(OssOidcIdentity(
                role_arn=role_arn, provider_arn=provider_arn,
                token_file=token_file, region=region, session_name=session_name,
            ))
        elif mode == "environment" and not any((role_arn, provider_arn, token_file)):
            credentials = None
        else:
            return None
        value = AliyunOssV2StorageAdapter(region=region, bucket=bucket,
                                        runtime_credentials=credentials)
    except (FileStorageError, OssRuntimeCredentialUnavailable):
        pass
    return value


def create_file_storage_adapter(settings: Settings):
    if not settings.file_storage_configuration_ready():
        return None
    return _storage_adapter(
        settings.file_storage_provider, settings.file_storage_region.strip(),
        settings.file_storage_bucket.strip(), settings.file_storage_credential_mode,
        settings.file_storage_oidc_role_arn, settings.file_storage_oidc_provider_arn,
        settings.file_storage_oidc_token_file, settings.file_storage_oidc_session_name,
    )
