"""Explicit OIDC credentials for OSS; no default-chain or static fallback.

The caller supplies reviewed non-secret identity coordinates. This module does
not create an issuer, token projector, RAM trust or any cloud resource. The
deployment must mount the projected token directory read-only and verify its
ownership. Credentials stay in memory and retain the SDK's actual expiration.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import PurePosixPath
import re
from threading import Lock
from typing import Callable

from .aliyun_sdk_logging import silence_aliyun_sdk_loggers


class OssRuntimeCredentialUnavailable(RuntimeError):
    """Fixed, non-sensitive error at the credential boundary."""


_ROLE = re.compile(r"acs:ram::([0-9]{16}):role/[a-zA-Z0-9.@_-]{1,64}", re.ASCII)
_PROVIDER = re.compile(r"acs:ram::([0-9]{16}):oidc-provider/[a-zA-Z0-9._-]{1,128}", re.ASCII)
_REGION = re.compile(r"[a-z][a-z0-9-]{1,61}[a-z0-9]", re.ASCII)
_SESSION = re.compile(r"[a-zA-Z0-9.@_-]{2,64}", re.ASCII)
_STATIC_ENV = (
    "OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET", "OSS_SESSION_TOKEN",
    "OSS_SECURITY_TOKEN", "ALIBABA_CLOUD_ACCESS_KEY_ID",
    "ALIBABA_CLOUD_ACCESS_KEY_SECRET", "ALIBABA_CLOUD_SECURITY_TOKEN",
)
_MARGIN = timedelta(seconds=60)


@dataclass(frozen=True, slots=True)
class OssOidcIdentity:
    role_arn: str
    provider_arn: str
    token_file: str
    region: str
    session_name: str = "rsc-pilot-files"

    def __post_init__(self):
        role = _ROLE.fullmatch(self.role_arn) if type(self.role_arn) is str else None
        provider = _PROVIDER.fullmatch(self.provider_arn) if type(self.provider_arn) is str else None
        if (role is None or provider is None or role[1] != provider[1]
                or type(self.region) is not str or _REGION.fullmatch(self.region) is None
                or type(self.session_name) is not str or _SESSION.fullmatch(self.session_name) is None
                or type(self.token_file) is not str or not self.token_file.startswith("/run/")
                or str(PurePosixPath(self.token_file)) != self.token_file
                or ".." in PurePosixPath(self.token_file).parts
                or any(ord(c) < 33 or ord(c) == 127 for c in self.token_file)):
            raise OssRuntimeCredentialUnavailable("OSS OIDC coordinates are invalid")


@dataclass(frozen=True, slots=True, repr=False)
class _TemporaryCredential:
    access_key_id: str
    access_key_secret: str
    security_token: str
    expires_at: datetime

    def to_oss(self):
        from alibabacloud_oss_v2.types import Credentials
        return Credentials(self.access_key_id, self.access_key_secret,
                           self.security_token, expiration=self.expires_at)


class _FrozenSigningCredential:
    def __init__(self, value: _TemporaryCredential, clock: Callable[[], datetime]):
        self._value, self._clock = value, clock

    def get_credentials(self):
        if self._clock() + _MARGIN >= self._value.expires_at:
            raise OssRuntimeCredentialUnavailable("OSS temporary credentials are unavailable")
        return self._value.to_oss()


def _utc_now():
    return datetime.now(timezone.utc)


def _sdk_provider(identity: OssOidcIdentity):
    silence_aliyun_sdk_loggers()
    from alibabacloud_credentials.http import HttpOptions
    from alibabacloud_credentials.provider.oidc import OIDCRoleArnCredentialsProvider
    silence_aliyun_sdk_loggers()
    # Direct explicit provider retains expiration, unlike the SDK's default
    # chain wrapper. It cannot select an environment AccessKey or CLI profile.
    return OIDCRoleArnCredentialsProvider(
        role_arn=identity.role_arn, oidc_provider_arn=identity.provider_arn,
        oidc_token_file_path=identity.token_file, role_session_name=identity.session_name,
        duration_seconds=3600, sts_region_id=identity.region,
        sts_endpoint=f"sts.{identity.region}.aliyuncs.com", enable_vpc=False,
        http_options=HttpOptions(connect_timeout=1000, read_timeout=2500),
    )


class OssOidcCredentialsProvider:
    """Serialized, refreshable SDK provider with an explicit expiry boundary."""

    def __init__(self, identity: OssOidcIdentity, *, provider_factory=_sdk_provider,
                 clock: Callable[[], datetime] = _utc_now):
        if type(identity) is not OssOidcIdentity or any(os.environ.get(k) for k in _STATIC_ENV):
            raise OssRuntimeCredentialUnavailable("OSS OIDC configuration is unavailable")
        self._clock, self._lock = clock, Lock()
        provider = None
        try:
            provider = provider_factory(identity)
        except Exception:
            pass
        if provider is None:
            # Raise outside the exception handler: even __context__ must not
            # retain an SDK exception which may contain a token or response.
            raise OssRuntimeCredentialUnavailable("OSS OIDC provider is unavailable")
        self._provider = provider

    def _snapshot(self) -> _TemporaryCredential:
        value = None
        with self._lock:
            silence_aliyun_sdk_loggers()
            try:
                raw = self._provider.get_credentials()
                now = self._clock()
                expiration = raw.get_expiration()
                strings = (raw.get_access_key_id(), raw.get_access_key_secret(), raw.get_security_token())
                if (raw.get_provider_name() == "oidc_role_arn" and type(expiration) is int
                        and all(type(s) is str and 1 <= len(s) <= 16384
                                and not any(ord(c) < 33 or ord(c) == 127 for c in s) for s in strings)
                        and now.tzinfo is not None and now.utcoffset() == timedelta(0)):
                    expires = datetime.fromtimestamp(expiration, timezone.utc)
                    if now + _MARGIN < expires <= now + timedelta(seconds=3660):
                        value = _TemporaryCredential(*strings, expires)
            except Exception:
                pass
        if value is None:
            raise OssRuntimeCredentialUnavailable("OSS temporary credentials are unavailable")
        return value

    def get_credentials(self):
        return self._snapshot().to_oss()

    def signing_snapshot(self, ttl_seconds: int):
        """Pin exactly one credential for the signed URL and its expiry."""
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 900:
            raise OssRuntimeCredentialUnavailable("OSS signing lifetime is invalid")
        value = self._snapshot()
        now = self._clock()
        expires = min(now + timedelta(seconds=ttl_seconds), value.expires_at - _MARGIN)
        if expires < now + timedelta(seconds=30):
            raise OssRuntimeCredentialUnavailable("OSS signing lifetime is unavailable")
        return _FrozenSigningCredential(value, self._clock), expires
