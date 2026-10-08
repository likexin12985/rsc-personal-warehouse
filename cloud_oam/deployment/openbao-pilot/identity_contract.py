"""Isolated Identity Token verification/projection prototype, not an app provider.

No credential discovery, HTTP, STS calls, signing key generation or daemon.
The caller supplies trusted public JWKS and an independently reviewed identity.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import jwt


class IdentityContractError(ValueError):
    """Stable safe code only; never include token or provider response content."""


@dataclass(frozen=True)
class IdentityContract:
    issuer: str
    audience: str
    subject: str
    maximum_ttl_seconds: int = 600
    minimum_remaining_seconds: int = 30

    def __post_init__(self):
        try:
            parsed=urlsplit(self.issuer)
            valid=(parsed.scheme=='https' and parsed.hostname and not parsed.username
                and not parsed.password and not parsed.query and not parsed.fragment
                and self.issuer.endswith('/v1/identity/oidc')
                and not any(c.isspace() for c in self.issuer)
                and isinstance(self.audience,str) and 1<=len(self.audience)<=256
                and not any(c.isspace() or c=='*' for c in self.audience)
                and str(UUID(self.subject))==self.subject
                and type(self.maximum_ttl_seconds) is int and 1<=self.maximum_ttl_seconds<=600
                and type(self.minimum_remaining_seconds) is int
                and 0<=self.minimum_remaining_seconds<self.maximum_ttl_seconds)
        except (TypeError,ValueError,AttributeError):
            valid=False
        if not valid:raise IdentityContractError('identity_contract_invalid')


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    audience: str
    subject: str
    key_id: str
    issued_at: int
    expires_at: int


def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            raise IdentityContractError('identity_token_duplicate_field')
        result[key]=value
    return result


def verify_identity_token(token: str, jwks: dict, contract: IdentityContract) -> VerifiedIdentity:
    """RS256 only; no claim-directed URL fetches, key fallback or clock leeway."""
    try:
        if not isinstance(token,str) or not 4<=len(token)<=20000:
            raise IdentityContractError('identity_token_format_invalid')
        parts=token.split('.')
        if len(parts)!=3 or not all(parts):
            raise IdentityContractError('identity_token_format_invalid')
        # Reject duplicate JSON claims/headers before PyJWT's normal JSON parser
        # can silently choose the last value. No claims are trusted at this point.
        for part in parts[:2]:
            value=json.loads(jwt.utils.base64url_decode(part),object_pairs_hook=_unique_object)
            if not isinstance(value,dict):
                raise IdentityContractError('identity_token_format_invalid')
        header=jwt.get_unverified_header(token)
        if (set(header)-{'alg','typ','kid'} or header.get('alg')!='RS256'
                or header.get('typ','JWT')!='JWT' or not isinstance(header.get('kid'),str)
                or not 1<=len(header['kid'])<=128):
            raise IdentityContractError('identity_token_header_invalid')
        keys=jwks.get('keys')
        if not isinstance(keys,list) or not 1<=len(keys)<=16:
            raise IdentityContractError('identity_jwks_invalid')
        ids=[key.get('kid') for key in keys if isinstance(key,dict)]
        if (len(ids)!=len(keys) or any(not isinstance(k,str) or not 1<=len(k)<=128 for k in ids)
                or len(set(ids))!=len(ids)):
            raise IdentityContractError('identity_jwks_ambiguous')
        matches=[key for key in keys if key.get('kid')==header['kid']]
        if len(matches)!=1:raise IdentityContractError('identity_kid_unknown')
        key=matches[0]
        if (key.get('kty')!='RSA' or key.get('alg')!='RS256' or key.get('use')!='sig'
                or set(key)&{'d','p','q','dp','dq','qi','oth','k'}):
            raise IdentityContractError('identity_jwk_invalid')
        public_key=jwt.PyJWK.from_dict(key,algorithm='RS256').key
        if public_key.key_size<2048:raise IdentityContractError('identity_jwk_invalid')
        claims=jwt.decode(token,public_key,algorithms=['RS256'],issuer=contract.issuer,
            audience=contract.audience,options={'require':['iss','aud','sub','iat','exp'],'strict_aud':True})
        if claims['sub']!=contract.subject:raise IdentityContractError('identity_subject_mismatch')
        if any(type(claims[name]) is not int or claims[name]<=0 for name in ('iat','exp')):
            raise IdentityContractError('identity_token_time_invalid')
        now=time.time()
        if not 0<claims['exp']-claims['iat']<=contract.maximum_ttl_seconds:
            raise IdentityContractError('identity_token_ttl_invalid')
        if claims['exp']-now<=contract.minimum_remaining_seconds:
            raise IdentityContractError('identity_token_remaining_too_short')
        return VerifiedIdentity(claims['iss'],claims['aud'],claims['sub'],header['kid'],claims['iat'],claims['exp'])
    except IdentityContractError:
        raise
    except (jwt.PyJWTError,TypeError,ValueError,AttributeError,KeyError,OverflowError,RecursionError):
        raise IdentityContractError('identity_token_verification_failed') from None


def project_identity_token(directory: Path, token: str, jwks: dict, contract: IdentityContract) -> VerifiedIdentity:
    """Atomically replace oidc-token in an already-owned private directory.

    The application mounts the directory read-only, never a single inode. The
    deployment must supply tmpfs; this prototype does not claim a mount is tmpfs.
    """
    verified=verify_identity_token(token,jwks,contract)
    directory_fd=None;temporary=None
    try:
        directory_fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        info=os.fstat(directory_fd)
        if info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700:
            raise IdentityContractError('identity_projection_directory_unsafe')
        try:
            target=os.stat('oidc-token',dir_fd=directory_fd,follow_symlinks=False)
        except FileNotFoundError:
            target=None
        if target and (not stat.S_ISREG(target.st_mode) or target.st_uid!=os.geteuid()
                or stat.S_IMODE(target.st_mode)!=0o600 or target.st_nlink!=1):
            raise IdentityContractError('identity_projection_target_unsafe')
        temporary='.oidc-'+uuid4().hex
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory_fd)
        with os.fdopen(fd,'w',encoding='ascii') as output:
            os.fchmod(output.fileno(),0o600)
            output.write(token);output.flush();os.fsync(output.fileno())
        # Slow storage must not commit a token that became unusable during write.
        verified=verify_identity_token(token,jwks,contract)
        os.replace(temporary,'oidc-token',src_dir_fd=directory_fd,dst_dir_fd=directory_fd)
        temporary=None
        os.fsync(directory_fd)
        return verified
    except IdentityContractError:
        raise
    except (OSError,UnicodeError,TypeError,ValueError):
        raise IdentityContractError('identity_projection_failed') from None
    finally:
        if directory_fd is not None:
            if temporary is not None:
                try:os.unlink(temporary,dir_fd=directory_fd)
                except OSError:pass
            try:os.close(directory_fd)
            except OSError:pass
