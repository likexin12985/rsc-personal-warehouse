from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import inspect
import json
import re
import uuid

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.formal_services import authentication_identity as subject
from app.formal_services.authentication_identity import (
    AuthenticationIdentityUnavailable,
    AuthenticationIdentityValidationError,
    compute_identity_hash,
    find_active_formal_identity,
    normalize_mobile,
    resolve_active_formal_identity,
)
from app.foundation_models import (
    AuthIdentity,
    Organization,
    Person,
    Role,
    RoleAssignment,
)
from app.models import User


NOW = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)
SECRET = "formal-authentication-identity-test-secret-v1"
MOBILE = "13800138000"
PROVIDER = "aliyun_pnvs"


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@dataclass
class IdentityWorld:
    organization: Organization
    person: Person
    user: User
    role: Role
    assignment: RoleAssignment
    identity: AuthIdentity
    identifier: str
    identity_type: str
    provider_key: str
    hash_version: int


def make_complete_identity(
    db: Session,
    *,
    identifier: str = MOBILE,
    identity_type: str = "mobile",
    provider_key: str = PROVIDER,
    hash_version: int = 1,
    identity_status: str = "active",
    account_status: str = "active",
    employment_status: str = "active",
    legacy_mobile: str = "13900000000",
) -> IdentityWorld:
    organization = Organization(
        id=uuid.uuid4(),
        code=f"ORG-{uuid.uuid4().hex[:12]}",
        name="正式身份测试区域",
        org_type="region_company",
        province_code="320000",
        status="active",
    )
    db.add(organization)
    db.flush()
    person = Person(
        id=uuid.uuid4(),
        organization_id=organization.id,
        employee_no=f"E-{uuid.uuid4().hex[:10]}",
        name="正式身份测试人员",
        employment_status=employment_status,
    )
    db.add(person)
    db.flush()
    user = User(
        id=str(uuid.uuid4()),
        person_id=person.id,
        account_status=account_status,
        authorization_version=1,
        mobile=legacy_mobile,
        name=person.name,
        password_hash="formal-password-login-disabled",
        role="admin",
        province="旧字段不得读取",
        is_active=True,
        require_password_change=False,
    )
    role = Role(
        id=uuid.uuid4(),
        code="technician",
        name="工程师",
        is_external=False,
        status="active",
    )
    db.add_all([user, role])
    db.flush()
    assignment = RoleAssignment(
        id=uuid.uuid4(),
        user_id=user.id,
        role_id=role.id,
        scope_type="person",
        scope_id=str(person.id),
        valid_from=NOW - timedelta(days=1),
        status="active",
        assigned_by=user.id,
        reason="formal authentication identity test",
    )
    verified_at = None if identity_status == "pending" else NOW - timedelta(minutes=1)
    revoked_at = NOW if identity_status == "revoked" else None
    identity = AuthIdentity(
        id=uuid.uuid4(),
        user_id=user.id,
        identity_type=identity_type,
        provider_key=provider_key,
        identifier_hash=compute_identity_hash(
            secret=SECRET,
            hash_version=hash_version,
            identity_type=identity_type,
            provider_key=provider_key,
            identifier=identifier,
        ),
        hash_version=hash_version,
        verified_at=verified_at,
        status=identity_status,
        revoked_at=revoked_at,
    )
    db.add_all([assignment, identity])
    db.flush()
    return IdentityWorld(
        organization=organization,
        person=person,
        user=user,
        role=role,
        assignment=assignment,
        identity=identity,
        identifier=identifier,
        identity_type=identity_type,
        provider_key=provider_key,
        hash_version=hash_version,
    )


def lookup_kwargs(world: IdentityWorld) -> dict[str, object]:
    return {
        "secret": SECRET,
        "hash_version": world.hash_version,
        "identity_type": world.identity_type,
        "provider_key": world.provider_key,
        "identifier": world.identifier,
        "now": NOW,
    }


def assert_unavailable(error: AuthenticationIdentityUnavailable) -> None:
    assert error.code == "authentication_identity_unavailable"
    assert str(error) == subject.PUBLIC_UNAVAILABLE_MESSAGE
    assert vars(error) == {}
    assert MOBILE not in str(error)
    assert "hash" not in str(error).lower()


def test_normalize_mobile_accepts_only_canonical_mainland_number() -> None:
    assert normalize_mobile(" 13800138000\n") == MOBILE

    invalid_values = (
        "",
        "12800138000",
        "1380013800",
        "138001380000",
        "+8613800138000",
        "008613800138000",
        "138 0013 8000",
        "１３８００１３８０００",
        "1380013800a",
    )
    for value in invalid_values:
        with pytest.raises(AuthenticationIdentityValidationError):
            normalize_mobile(value)
    with pytest.raises(AuthenticationIdentityValidationError):
        normalize_mobile(13800138000)  # type: ignore[arg-type]


@pytest.mark.parametrize("secret", ["", "x" * 31, " " * 32, None, b"x" * 32])
def test_compute_identity_hash_rejects_invalid_secret(secret: object) -> None:
    with pytest.raises(AuthenticationIdentityValidationError) as caught:
        compute_identity_hash(
            secret=secret,  # type: ignore[arg-type]
            hash_version=1,
            identity_type="mobile",
            provider_key=PROVIDER,
            identifier=MOBILE,
        )
    assert SECRET not in str(caught.value)


def test_compute_identity_hash_is_canonical_hmac_sha256() -> None:
    exact_secret = "s" * 32
    canonical = json.dumps(
        [1, "mobile", PROVIDER, MOBILE],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    expected = hmac.new(exact_secret.encode(), canonical, hashlib.sha256).hexdigest()

    actual = compute_identity_hash(
        secret=exact_secret,
        hash_version=1,
        identity_type="mobile",
        provider_key=PROVIDER,
        identifier=f" {MOBILE} ",
    )

    assert actual == expected
    assert re.fullmatch(r"[0-9a-f]{64}", actual)
    assert actual != hashlib.sha256(canonical).hexdigest()


def test_hash_domain_separates_version_type_provider_and_identifier() -> None:
    base = compute_identity_hash(
        secret=SECRET,
        hash_version=1,
        identity_type="wechat_unionid",
        provider_key="wx-app-one",
        identifier="wx-subject-one",
    )
    variants = {
        compute_identity_hash(
            secret=SECRET,
            hash_version=2,
            identity_type="wechat_unionid",
            provider_key="wx-app-one",
            identifier="wx-subject-one",
        ),
        compute_identity_hash(
            secret=SECRET,
            hash_version=1,
            identity_type="wechat_openid",
            provider_key="wx-app-one",
            identifier="wx-subject-one",
        ),
        compute_identity_hash(
            secret=SECRET,
            hash_version=1,
            identity_type="wechat_unionid",
            provider_key="wx-app-two",
            identifier="wx-subject-one",
        ),
        compute_identity_hash(
            secret=SECRET,
            hash_version=1,
            identity_type="wechat_unionid",
            provider_key="wx-app-one",
            identifier="wx-subject-two",
        ),
    }
    assert base not in variants
    assert len(variants) == 4


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("identity_type", "email"),
        ("identity_type", "Mobile"),
        ("identity_type", " mobile"),
        ("provider_key", ""),
        ("provider_key", " provider"),
        ("provider_key", "provider\nkey"),
        ("provider_key", "p" * 101),
        ("hash_version", 0),
        ("hash_version", -1),
        ("hash_version", True),
        ("hash_version", 2_147_483_648),
    ],
)
def test_hash_rejects_unsafe_domain_components(field: str, value: object) -> None:
    values: dict[str, object] = {
        "secret": SECRET,
        "hash_version": 1,
        "identity_type": "mobile",
        "provider_key": PROVIDER,
        "identifier": MOBILE,
    }
    values[field] = value
    with pytest.raises(AuthenticationIdentityValidationError):
        compute_identity_hash(**values)  # type: ignore[arg-type]


def test_wechat_identifiers_are_exact_and_openid_rejects_mobile_substitution() -> None:
    with pytest.raises(AuthenticationIdentityValidationError):
        compute_identity_hash(
            secret=SECRET,
            hash_version=1,
            identity_type="wechat_openid",
            provider_key="wx-app",
            identifier=MOBILE,
        )
    for value in ("", " openid", "openid\nvalue", "x" * 513):
        with pytest.raises(AuthenticationIdentityValidationError):
            compute_identity_hash(
                secret=SECRET,
                hash_version=1,
                identity_type="wechat_openid",
                provider_key="wx-app",
                identifier=value,
            )


def test_exact_lookup_returns_complete_formal_principal_without_secret_fields(
    db: Session,
) -> None:
    world = make_complete_identity(db)

    resolved = resolve_active_formal_identity(db, **lookup_kwargs(world))

    assert resolved.identity_id == world.identity.id
    assert resolved.user_id == world.user.id
    assert resolved.person_id == world.person.id
    assert resolved.identity_type == "mobile"
    assert resolved.provider_key == PROVIDER
    assert resolved.hash_version == 1
    assert resolved.principal.user_id == world.user.id
    assert resolved.principal.person_id == world.person.id
    assert resolved.principal.access_mode == "active"
    assert not hasattr(resolved, "identifier")
    assert not hasattr(resolved, "identifier_hash")
    assert MOBILE not in repr(resolved)
    assert world.identity.identifier_hash not in repr(resolved)


def test_restricted_handover_is_a_complete_formal_principal(db: Session) -> None:
    world = make_complete_identity(
        db,
        account_status="restricted_handover",
        employment_status="left",
    )

    resolved = resolve_active_formal_identity(db, **lookup_kwargs(world))

    assert resolved.principal.access_mode == "restricted_handover"
    assert resolved.principal.employment_status == "left"


def test_optional_unknown_and_required_unknown_share_non_enumerating_outcome(
    db: Session,
) -> None:
    assert (
        find_active_formal_identity(
            db,
            secret=SECRET,
            hash_version=1,
            identity_type="mobile",
            provider_key=PROVIDER,
            identifier=MOBILE,
            now=NOW,
        )
        is None
    )
    with pytest.raises(AuthenticationIdentityUnavailable) as caught:
        resolve_active_formal_identity(
            db,
            secret=SECRET,
            hash_version=1,
            identity_type="mobile",
            provider_key=PROVIDER,
            identifier=MOBILE,
            now=NOW,
        )
    assert_unavailable(caught.value)


@pytest.mark.parametrize("identity_status", ["pending", "revoked"])
def test_pending_and_revoked_identity_are_indistinguishable_from_unknown(
    db: Session,
    identity_status: str,
) -> None:
    world = make_complete_identity(db, identity_status=identity_status)

    assert find_active_formal_identity(db, **lookup_kwargs(world)) is None
    with pytest.raises(AuthenticationIdentityUnavailable) as caught:
        resolve_active_formal_identity(db, **lookup_kwargs(world))
    assert_unavailable(caught.value)


def test_provider_version_and_secret_mismatch_never_fall_back(db: Session) -> None:
    world = make_complete_identity(db, hash_version=2)
    base = lookup_kwargs(world)
    variants = (
        {**base, "provider_key": "other-provider"},
        {**base, "hash_version": 1},
        {**base, "secret": "different-formal-authentication-secret-v2"},
    )
    for values in variants:
        assert find_active_formal_identity(db, **values) is None


def test_wechat_unionid_cannot_satisfy_openid_lookup(db: Session) -> None:
    subject_value = "wechat-provider-subject-001"
    world = make_complete_identity(
        db,
        identifier=subject_value,
        identity_type="wechat_unionid",
        provider_key="wx-rsc-app",
    )

    assert resolve_active_formal_identity(db, **lookup_kwargs(world)).identity_id == (
        world.identity.id
    )
    assert (
        find_active_formal_identity(
            db,
            secret=SECRET,
            hash_version=1,
            identity_type="wechat_openid",
            provider_key="wx-rsc-app",
            identifier=subject_value,
            now=NOW,
        )
        is None
    )


def test_legacy_mobile_match_cannot_replace_formal_hash_match(db: Session) -> None:
    formal_mobile = "13900139000"
    world = make_complete_identity(
        db,
        identifier=formal_mobile,
        legacy_mobile=MOBILE,
    )

    assert (
        find_active_formal_identity(
            db,
            secret=SECRET,
            hash_version=1,
            identity_type="mobile",
            provider_key=PROVIDER,
            identifier=MOBILE,
            now=NOW,
        )
        is None
    )
    assert resolve_active_formal_identity(db, **lookup_kwargs(world)).user_id == (
        world.user.id
    )


@pytest.mark.parametrize("broken_edge", ["person", "account", "role", "scope"])
def test_broken_formal_principal_fails_with_one_public_error(
    db: Session,
    broken_edge: str,
) -> None:
    world = make_complete_identity(db)
    if broken_edge == "person":
        world.user.person_id = None
    elif broken_edge == "account":
        world.user.account_status = "suspended"
    elif broken_edge == "role":
        world.assignment.status = "revoked"
        world.assignment.revoked_at = NOW
        world.assignment.revoked_by = world.user.id
    else:
        world.assignment.scope_id = str(uuid.uuid4())
    db.flush()

    with pytest.raises(AuthenticationIdentityUnavailable) as caught:
        resolve_active_formal_identity(db, **lookup_kwargs(world))

    assert_unavailable(caught.value)
    assert caught.value.__cause__ is None


class AmbiguousIdentitySession:
    no_autoflush = nullcontext()

    def execute(self, _statement):
        return AmbiguousRows()


class AmbiguousRows:
    def all(self):
        return [
            (uuid.uuid4(), "user-one"),
            (uuid.uuid4(), "user-two"),
        ]


def test_ambiguous_exact_identity_fails_closed_without_detail() -> None:
    with pytest.raises(AuthenticationIdentityUnavailable) as caught:
        resolve_active_formal_identity(
            AmbiguousIdentitySession(),  # type: ignore[arg-type]
            secret=SECRET,
            hash_version=1,
            identity_type="mobile",
            provider_key=PROVIDER,
            identifier=MOBILE,
            now=NOW,
        )
    assert_unavailable(caught.value)


def test_lookup_does_not_flush_commit_or_mutate_identity(db: Session) -> None:
    world = make_complete_identity(db)
    original_hash = world.identity.identifier_hash
    original_updated_at = world.identity.updated_at
    identity_count = db.scalar(select(func.count()).select_from(AuthIdentity))
    pending_user = User(
        id=str(uuid.uuid4()),
        mobile="13700137000",
        name="不得被自动刷新",
        password_hash="disabled",
        role="technician",
        is_active=True,
        require_password_change=False,
    )
    db.add(pending_user)
    assert pending_user in db.new

    resolved = resolve_active_formal_identity(db, **lookup_kwargs(world))

    assert resolved.identity_id == world.identity.id
    assert pending_user in db.new
    assert db.scalar(select(func.count()).select_from(AuthIdentity)) == identity_count
    assert world.identity.identifier_hash == original_hash
    assert world.identity.updated_at == original_updated_at
    assert world.identity not in db.dirty


def test_module_has_no_legacy_identity_or_write_dependency() -> None:
    source = inspect.getsource(subject)
    assert "WechatIdentity" not in source
    assert "SmsLoginChallenge" not in source
    assert "User.mobile" not in source
    assert ".commit(" not in source
    assert "db.add(" not in source
    assert "db.delete(" not in source
