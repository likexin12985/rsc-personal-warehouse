from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from starlette.requests import Request

from app import dependencies
from app.database import Base
from app.formal_access import FormalAccessError, load_formal_principal
from app.foundation_models import (
    AuthIdentity,
    Organization,
    Permission,
    Person,
    Role,
    RoleAssignment,
    RolePermission,
)
from app.models import AuthSession, User
from app.security import create_access_token


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def make_organization(
    db: Session,
    *,
    name: str,
    parent: Organization | None = None,
    status: str = "active",
    org_type: str | None = None,
) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        code=f"ORG-{uuid.uuid4().hex[:12]}",
        name=name,
        parent_id=parent.id if parent else None,
        org_type=org_type or ("region_company" if parent else "headquarters"),
        province_code=None,
        status=status,
    )
    db.add(organization)
    db.flush()
    return organization


def make_person(
    db: Session,
    organization: Organization,
    *,
    name: str,
    employment_status: str = "active",
) -> Person:
    person = Person(
        id=uuid.uuid4(),
        organization_id=organization.id,
        employee_no=f"E-{uuid.uuid4().hex[:10]}",
        name=name,
        employment_status=employment_status,
    )
    db.add(person)
    db.flush()
    return person


def make_user(
    db: Session,
    organization: Organization,
    *,
    name: str,
    account_status: str = "active",
    employment_status: str = "active",
    bind_person: bool = True,
    add_identity: bool = True,
    legacy_is_active: bool = True,
) -> tuple[User, Person | None]:
    person = (
        make_person(
            db,
            organization,
            name=name,
            employment_status=employment_status,
        )
        if bind_person
        else None
    )
    user = User(
        id=str(uuid.uuid4()),
        person_id=person.id if person else None,
        account_status=account_status,
        authorization_version=1,
        mobile=f"1{uuid.uuid4().hex[:19]}",
        name=name,
        password_hash="formal-password-login-disabled",
        role="technician",
        province=None,
        is_active=legacy_is_active,
        require_password_change=False,
    )
    db.add(user)
    db.flush()
    if add_identity:
        db.add(
            AuthIdentity(
                id=uuid.uuid4(),
                user_id=user.id,
                identity_type="mobile",
                provider_key="test",
                identifier_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                hash_version=1,
                verified_at=NOW,
                status="active",
                revoked_at=None,
            )
        )
        db.flush()
    return user, person


def make_role(db: Session, code: str) -> Role:
    role = Role(
        id=uuid.uuid4(),
        code=code,
        name=code,
        is_external=code == "star_headquarters_approver",
        status="active",
    )
    db.add(role)
    db.flush()
    return role


def make_permission(db: Session, resource: str, action: str) -> Permission:
    permission = Permission(
        id=uuid.uuid4(),
        resource=resource,
        action=action,
        field_code="",
        description=f"{resource}:{action}",
    )
    db.add(permission)
    db.flush()
    return permission


def grant(
    db: Session,
    role: Role,
    permission: Permission,
    *,
    effect: str = "allow",
) -> None:
    db.add(
        RolePermission(
            id=uuid.uuid4(),
            role_id=role.id,
            permission_id=permission.id,
            effect=effect,
        )
    )
    db.flush()


def assign(
    db: Session,
    user: User,
    role: Role,
    *,
    scope_type: str,
    scope_id: str,
    status: str = "active",
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    revoked_at: datetime | None = None,
) -> RoleAssignment:
    assignment = RoleAssignment(
        id=uuid.uuid4(),
        user_id=user.id,
        role_id=role.id,
        scope_type=scope_type,
        scope_id=scope_id,
        valid_from=valid_from or NOW - timedelta(days=1),
        valid_to=valid_to,
        status=status,
        assigned_by=user.id,
        revoked_at=revoked_at,
        revoked_by=user.id if revoked_at is not None else None,
        reason="formal access test",
    )
    db.add(assignment)
    db.flush()
    return assignment


def authenticated_request(
    db: Session,
    user: User,
    *,
    path: str = "/api/access/context",
) -> tuple[Request, str]:
    request_time = datetime.now(timezone.utc)
    session = AuthSession(
        id=str(uuid.uuid4()),
        user_id=user.id,
        refresh_token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        client_type="web",
        device_id=f"device-{uuid.uuid4().hex}",
        device_name="formal access test",
        ip_address="hmac:1:" + "a" * 64,
        created_at=request_time,
        last_seen_at=request_time,
        expires_at=request_time + timedelta(days=1),
    )
    db.add(session)
    db.flush()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )
    return request, create_access_token(user.id, session.id)


def test_admin_national_scope_covers_every_organization(db: Session):
    headquarters = make_organization(db, name="总部")
    province = make_organization(db, name="江苏区域", parent=headquarters)
    user, _person = make_user(db, headquarters, name="全国管理员")
    role = make_role(db, "admin")
    permission = make_permission(db, "inventory", "read")
    grant(db, role, permission)
    assign(db, user, role, scope_type="national", scope_id="*")

    principal = load_formal_principal(db, user.id, now=NOW)

    assert principal.role_codes == ("admin",)
    assert principal.access_mode == "active"
    assert principal.allows(
        db,
        "inventory",
        "read",
        target_scope_type="organization",
        target_scope_id=str(province.id),
    )


def test_organization_scope_includes_descendants_but_not_siblings(db: Session):
    headquarters = make_organization(db, name="总部")
    jiangsu = make_organization(db, name="江苏区域", parent=headquarters)
    jiangsu_team = make_organization(db, name="江苏服务团队", parent=jiangsu)
    zhejiang = make_organization(db, name="浙江区域", parent=headquarters)
    zhejiang_team = make_organization(db, name="浙江服务团队", parent=zhejiang)
    user, _person = make_user(db, jiangsu, name="江苏物资管理员")
    jiangsu_engineer = make_person(db, jiangsu_team, name="江苏工程师")
    zhejiang_engineer = make_person(db, zhejiang_team, name="浙江工程师")
    role = make_role(db, "provincial_manager")
    permission = make_permission(db, "inventory", "read")
    grant(db, role, permission)
    assign(
        db,
        user,
        role,
        scope_type="organization",
        scope_id=str(jiangsu.id),
    )

    principal = load_formal_principal(db, user.id, now=NOW)

    assert principal.allows(
        db,
        "inventory",
        "read",
        target_scope_type="organization",
        target_scope_id=str(jiangsu_team.id),
    )
    assert principal.allows(
        db,
        "inventory",
        "read",
        target_scope_type="person",
        target_scope_id=str(jiangsu_engineer.id),
    )
    assert not principal.allows(
        db,
        "inventory",
        "read",
        target_scope_type="organization",
        target_scope_id=str(zhejiang_team.id),
    )
    assert not principal.allows(
        db,
        "inventory",
        "read",
        target_scope_type="person",
        target_scope_id=str(zhejiang_engineer.id),
    )


def test_technician_scope_is_exactly_the_bound_person(db: Session):
    organization = make_organization(db, name="江苏区域")
    user, person = make_user(db, organization, name="工程师本人")
    colleague = make_person(db, organization, name="同组织工程师")
    role = make_role(db, "technician")
    permission = make_permission(db, "inventory", "read")
    grant(db, role, permission)
    assert person is not None
    assign(db, user, role, scope_type="person", scope_id=str(person.id))

    principal = load_formal_principal(db, user.id, now=NOW)

    assert principal.allows(
        db,
        "inventory",
        "read",
        target_scope_type="person",
        target_scope_id=str(person.id),
    )
    assert not principal.allows(
        db,
        "inventory",
        "read",
        target_scope_type="person",
        target_scope_id=str(colleague.id),
    )


def test_external_approver_has_exact_document_scope_and_no_inventory_access(
    db: Session,
):
    organization = make_organization(
        db,
        name="星星充电审批组织",
        org_type="external_approval_org",
    )
    user, _person = make_user(db, organization, name="外部审批人")
    role = make_role(db, "star_headquarters_approver")
    access_context = make_permission(db, "access_context", "read")
    approval = make_permission(
        db,
        "material_request_approval",
        "decide_level_3",
    )
    grant(db, role, access_context)
    grant(db, role, approval)
    assign(
        db,
        user,
        role,
        scope_type="document",
        scope_id="material-request:MR-001",
    )

    principal = load_formal_principal(db, user.id, now=NOW)

    assert principal.allows(db, "access_context", "read")
    assert principal.allows(
        db,
        "material_request_approval",
        "decide_level_3",
        target_scope_type="document",
        target_scope_id="material-request:MR-001",
    )
    assert not principal.allows(
        db,
        "material_request_approval",
        "decide_level_3",
        target_scope_type="document",
        target_scope_id="material-request:MR-002",
    )
    assert not principal.allows(db, "inventory", "read")


def test_deny_wins_for_the_same_target_without_erasing_other_scope_allows(
    db: Session,
):
    headquarters = make_organization(db, name="总部")
    jiangsu = make_organization(db, name="江苏区域", parent=headquarters)
    jiangsu_team = make_organization(db, name="江苏团队", parent=jiangsu)
    zhejiang = make_organization(db, name="浙江区域", parent=headquarters)
    user, _person = make_user(db, headquarters, name="复合授权人员")
    admin = make_role(db, "admin")
    provincial = make_role(db, "provincial_manager")
    permission = make_permission(db, "inventory", "read")
    grant(db, admin, permission, effect="allow")
    grant(db, provincial, permission, effect="deny")
    assign(db, user, admin, scope_type="national", scope_id="*")
    assign(
        db,
        user,
        provincial,
        scope_type="organization",
        scope_id=str(jiangsu.id),
    )

    principal = load_formal_principal(db, user.id, now=NOW)

    assert not principal.allows(
        db,
        "inventory",
        "read",
        target_scope_type="organization",
        target_scope_id=str(jiangsu_team.id),
    )
    assert principal.allows(
        db,
        "inventory",
        "read",
        target_scope_type="organization",
        target_scope_id=str(zhejiang.id),
    )


@pytest.mark.parametrize("variant", ["future", "expired", "revoked"])
def test_non_current_assignments_do_not_establish_a_principal(
    db: Session,
    variant: str,
):
    organization = make_organization(db, name=f"{variant}授权组织")
    user, _person = make_user(db, organization, name=f"{variant}授权人员")
    role = make_role(db, "admin")

    if variant == "future":
        assign(
            db,
            user,
            role,
            scope_type="national",
            scope_id="*",
            status="scheduled",
            valid_from=NOW + timedelta(minutes=1),
            valid_to=NOW + timedelta(days=1),
        )
    elif variant == "expired":
        assign(
            db,
            user,
            role,
            scope_type="national",
            scope_id="*",
            valid_from=NOW - timedelta(days=2),
            valid_to=NOW - timedelta(minutes=1),
        )
    else:
        assign(
            db,
            user,
            role,
            scope_type="national",
            scope_id="*",
            status="revoked",
            revoked_at=NOW - timedelta(minutes=1),
        )

    with pytest.raises(FormalAccessError, match="没有当前有效的角色授权"):
        load_formal_principal(db, user.id, now=NOW)


@pytest.mark.parametrize(
    ("missing_boundary", "message"),
    [
        ("person", "尚未完成唯一人员绑定"),
        ("identity", "没有已验证的正式登录身份"),
        ("assignment", "没有当前有效的角色授权"),
    ],
)
def test_missing_formal_identity_boundaries_fail_closed(
    db: Session,
    missing_boundary: str,
    message: str,
):
    organization = make_organization(db, name=f"缺失{missing_boundary}组织")
    user, _person = make_user(
        db,
        organization,
        name=f"缺失{missing_boundary}人员",
        bind_person=missing_boundary != "person",
        add_identity=missing_boundary != "identity",
    )
    if missing_boundary != "assignment":
        # A role row is deliberately not enough: the missing identity boundary
        # must stop evaluation before permissions can be inferred.
        make_role(db, "admin")

    with pytest.raises(FormalAccessError, match=message):
        load_formal_principal(db, user.id, now=NOW)


@pytest.mark.parametrize(
    ("account_status", "employment_status"),
    [
        ("restricted_handover", "active"),
        ("active", "left"),
        ("active", "inactive"),
    ],
)
def test_restricted_handover_allows_only_self_service_resources(
    db: Session,
    account_status: str,
    employment_status: str,
):
    organization = make_organization(db, name="交接组织")
    user, _person = make_user(
        db,
        organization,
        name="交接人员",
        account_status=account_status,
        employment_status=employment_status,
    )
    role = make_role(db, "admin")
    permissions = [
        make_permission(db, "account", "read_self"),
        make_permission(db, "access_context", "read"),
        make_permission(db, "auth_session", "manage"),
        make_permission(db, "handover", "read"),
        make_permission(db, "inventory", "read"),
    ]
    for permission in permissions:
        grant(db, role, permission)
    assign(db, user, role, scope_type="national", scope_id="*")

    principal = load_formal_principal(db, user.id, now=NOW)

    assert principal.access_mode == "restricted_handover"
    assert principal.allows(db, "account", "read_self")
    assert principal.allows(db, "access_context", "read")
    assert principal.allows(db, "auth_session", "manage")
    assert principal.allows(db, "handover", "read")
    assert not principal.allows(db, "inventory", "read")


def test_legacy_is_active_flag_cannot_block_formal_restricted_handover(
    db: Session,
):
    organization = make_organization(db, name="旧字段不再权威的交接组织")
    user, _person = make_user(
        db,
        organization,
        name="正式交接人员",
        account_status="active",
        employment_status="left",
        legacy_is_active=False,
    )
    role = make_role(db, "admin")
    account_permission = make_permission(db, "account", "read_self")
    inventory_permission = make_permission(db, "inventory", "read")
    grant(db, role, account_permission)
    grant(db, role, inventory_permission)
    assign(db, user, role, scope_type="national", scope_id="*")

    principal = load_formal_principal(db, user.id, now=NOW)

    assert principal.access_mode == "restricted_handover"
    assert principal.allows(db, "account", "read_self")
    assert not principal.allows(db, "inventory", "read")


@pytest.mark.parametrize(
    "account_status",
    ["pending_identity", "suspended", "disabled"],
)
def test_formal_blocked_account_statuses_are_rejected(
    db: Session,
    account_status: str,
):
    organization = make_organization(db, name=f"{account_status}账号组织")
    user, _person = make_user(
        db,
        organization,
        name=f"{account_status}账号",
        account_status=account_status,
    )

    with pytest.raises(FormalAccessError, match="账号状态不允许"):
        load_formal_principal(db, user.id, now=NOW)


@pytest.mark.parametrize(
    ("role_code", "scope_type", "scope_id_factory"),
    [
        ("admin", "organization", lambda organization, _person: str(organization.id)),
        ("provincial_manager", "national", lambda _organization, _person: "*"),
        ("technician", "person", lambda _organization, _person: str(uuid.uuid4())),
        (
            "star_headquarters_approver",
            "document",
            lambda _organization, _person: "   ",
        ),
    ],
)
def test_role_scope_mismatches_fail_the_whole_principal(
    db: Session,
    role_code: str,
    scope_type: str,
    scope_id_factory,
):
    organization = make_organization(db, name=f"{role_code}非法范围组织")
    user, person = make_user(db, organization, name=f"{role_code}非法范围人员")
    role = make_role(db, role_code)
    assign(
        db,
        user,
        role,
        scope_type=scope_type,
        scope_id=scope_id_factory(organization, person),
    )

    with pytest.raises(FormalAccessError, match="数据范围绑定无效"):
        load_formal_principal(db, user.id, now=NOW)


def test_inactive_organization_cannot_back_a_provincial_assignment(db: Session):
    inactive = make_organization(db, name="已停用区域", status="inactive")
    user, _person = make_user(db, inactive, name="停用区域管理员")
    role = make_role(db, "provincial_manager")
    assign(
        db,
        user,
        role,
        scope_type="organization",
        scope_id=str(inactive.id),
    )

    with pytest.raises(FormalAccessError, match="数据范围绑定无效"):
        load_formal_principal(db, user.id, now=NOW)


def test_admin_role_requires_a_headquarters_person(db: Session):
    headquarters = make_organization(db, name="总部")
    region = make_organization(db, name="江苏区域", parent=headquarters)
    user, _person = make_user(db, region, name="非总部管理员候选")
    role = make_role(db, "admin")
    assign(db, user, role, scope_type="national", scope_id="*")

    with pytest.raises(FormalAccessError, match="数据范围绑定无效"):
        load_formal_principal(db, user.id, now=NOW)


def test_provincial_scope_requires_an_active_region_company(db: Session):
    headquarters = make_organization(db, name="总部")
    region = make_organization(db, name="江苏区域", parent=headquarters)
    department = make_organization(
        db,
        name="江苏服务部门",
        parent=region,
        org_type="department",
    )
    user, _person = make_user(db, headquarters, name="区域授权候选")
    role = make_role(db, "provincial_manager")
    assign(
        db,
        user,
        role,
        scope_type="organization",
        scope_id=str(department.id),
    )

    with pytest.raises(FormalAccessError, match="数据范围绑定无效"):
        load_formal_principal(db, user.id, now=NOW)


def test_external_identity_cannot_receive_technician_role(db: Session):
    external = make_organization(
        db,
        name="星星充电审批组织",
        org_type="external_approval_org",
    )
    user, person = make_user(db, external, name="外部审批人员")
    assert person is not None
    role = make_role(db, "technician")
    assign(db, user, role, scope_type="person", scope_id=str(person.id))

    with pytest.raises(FormalAccessError, match="数据范围绑定无效"):
        load_formal_principal(db, user.id, now=NOW)


def test_internal_identity_cannot_receive_external_approver_role(db: Session):
    headquarters = make_organization(db, name="总部")
    user, _person = make_user(db, headquarters, name="内部人员")
    role = make_role(db, "star_headquarters_approver")
    assign(
        db,
        user,
        role,
        scope_type="document",
        scope_id="material-request:MR-001",
    )

    with pytest.raises(FormalAccessError, match="数据范围绑定无效"):
        load_formal_principal(db, user.id, now=NOW)


def test_production_request_reloads_formal_authorization_on_every_request(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    organization = make_organization(db, name="逐请求收权组织")
    user, person = make_user(db, organization, name="逐请求收权人员")
    assert person is not None
    role = make_role(db, "technician")
    permission = make_permission(db, "access_context", "read")
    grant(db, role, permission)
    assignment = assign(
        db,
        user,
        role,
        scope_type="person",
        scope_id=str(person.id),
    )
    request, token = authenticated_request(db, user)
    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda: SimpleNamespace(
            environment="production",
            identity_hash_version=1,
        ),
    )

    resolved = dependencies.get_current_user(
        request,
        db,
        access_token=token,
        authorization=None,
    )
    assert resolved.id == user.id
    assert request.state.formal_principal.user_id == user.id

    assignment.status = "revoked"
    assignment.revoked_at = NOW
    assignment.revoked_by = user.id
    db.flush()
    next_request = Request(dict(request.scope))
    with pytest.raises(HTTPException) as error:
        dependencies.get_current_user(
            next_request,
            db,
            access_token=token,
            authorization=None,
        )
    assert error.value.status_code == 403
    assert error.value.detail == "账号当前没有有效的正式访问权限"


def test_production_request_rejects_active_plaintext_session_without_rewriting_it(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    organization = make_organization(db, name="会话来源证据组织")
    user, person = make_user(db, organization, name="会话来源证据人员")
    assert person is not None
    role = make_role(db, "technician")
    grant(db, role, make_permission(db, "access_context", "read"))
    assign(db, user, role, scope_type="person", scope_id=str(person.id))
    request, token = authenticated_request(db, user)
    auth_session = db.scalar(
        select(AuthSession).where(AuthSession.user_id == user.id)
    )
    assert auth_session is not None
    auth_session.ip_address = "198.51.100.99"
    db.flush()
    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda: SimpleNamespace(
            environment="production",
            identity_hash_version=1,
        ),
    )

    with pytest.raises(HTTPException) as error:
        dependencies.get_current_user(
            request,
            db,
            access_token=token,
            authorization=None,
        )

    assert error.value.status_code == 401
    assert error.value.detail == "登录已失效"
    assert auth_session.ip_address == "198.51.100.99"
    assert auth_session.revoked_at is None


def test_production_request_keeps_formal_restricted_handover_login(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    organization = make_organization(db, name="交接逐请求组织")
    user, person = make_user(
        db,
        organization,
        name="交接逐请求人员",
        employment_status="left",
        legacy_is_active=False,
    )
    assert person is not None
    role = make_role(db, "technician")
    permission = make_permission(db, "access_context", "read")
    grant(db, role, permission)
    assign(db, user, role, scope_type="person", scope_id=str(person.id))
    request, token = authenticated_request(db, user)
    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda: SimpleNamespace(
            environment="production",
            identity_hash_version=1,
        ),
    )

    dependencies.get_current_user(
        request,
        db,
        access_token=token,
        authorization=None,
    )

    assert request.state.formal_principal.access_mode == "restricted_handover"
    assert request.state.formal_principal.allows(db, "access_context", "read")
    assert not request.state.formal_principal.allows(db, "inventory", "read")


def test_production_role_dependency_uses_formal_roles_not_legacy_role(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    organization = make_organization(db, name="正式角色依赖组织")
    user, _person = make_user(db, organization, name="正式管理员")
    assert user.role == "technician"
    role = make_role(db, "admin")
    grant(db, role, make_permission(db, "access_context", "read"))
    assign(db, user, role, scope_type="national", scope_id="*")
    request, token = authenticated_request(db, user)
    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda: SimpleNamespace(
            environment="production",
            identity_hash_version=1,
        ),
    )
    dependencies.get_current_user(
        request,
        db,
        access_token=token,
        authorization=None,
    )

    assert dependencies.require_roles("admin")(request, user) is user
    with pytest.raises(HTTPException) as error:
        dependencies.require_roles("technician")(request, user)
    assert error.value.status_code == 403


def test_formal_principal_dependency_reuses_request_snapshot(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    organization = make_organization(db, name="权限快照组织")
    user, _person = make_user(db, organization, name="权限快照管理员")
    role = make_role(db, "admin")
    grant(db, role, make_permission(db, "access_context", "read"))
    assign(db, user, role, scope_type="national", scope_id="*")
    request, token = authenticated_request(db, user)
    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda: SimpleNamespace(
            environment="production",
            identity_hash_version=1,
        ),
    )
    dependencies.get_current_user(
        request,
        db,
        access_token=token,
        authorization=None,
    )

    assert (
        dependencies.get_formal_principal(request, user, db)
        is request.state.formal_principal
    )
