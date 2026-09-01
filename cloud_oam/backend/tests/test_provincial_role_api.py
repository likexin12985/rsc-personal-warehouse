from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_services import provincial_role_assignment as provincial_roles
from app.routers import access


NOW = datetime(2026, 8, 30, 10, 0, tzinfo=timezone.utc)
REGION_ID = uuid.UUID("40000000-0000-4000-8000-000000000001")
PERSON_ID = uuid.UUID("40000000-0000-4000-8000-000000000002")
ASSIGNMENT_ID = uuid.UUID("40000000-0000-4000-8000-000000000003")


class _Principal:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed

    def allows(self, _db, _resource: str, _action: str, **_kwargs) -> bool:
        return self.allowed


@pytest.fixture
def api_client():
    db = SimpleNamespace(commit=Mock(), rollback=Mock())
    principal = _Principal()
    api = FastAPI()
    api.include_router(access.router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_formal_principal] = lambda: principal
    with TestClient(api) as client:
        yield client, db, principal


def _headers(**overrides: str) -> dict[str, str]:
    result = {
        "Idempotency-Key": "provincial-grant-0001",
        "X-Request-ID": "request-0001",
    }
    result.update(overrides)
    return result


def _grant_payload(**overrides: object) -> dict[str, object]:
    result: dict[str, object] = {
        "person_id": str(PERSON_ID),
        "organization_id": str(REGION_ID),
        "expected_authorization_version": 7,
        "valid_to": None,
        "reason": "总部管理员确认配置江苏省背包负责人",
    }
    result.update(overrides)
    return result


def _mutation_result(*, replayed: bool = False, status: str = "active"):
    return provincial_roles.ProvincialRoleAssignmentResult(
        assignment_id=ASSIGNMENT_ID,
        target_user_id="user-id-must-not-be-exposed",
        target_person_id=PERSON_ID,
        organization_id=REGION_ID,
        role_code="provincial_manager",
        scope_type="organization",
        status=status,
        valid_from=NOW,
        valid_to=None,
        authorization_version=8,
        audit_event_id=uuid.uuid4(),
        state_transition_event_id=uuid.uuid4(),
        replayed=replayed,
    )


def test_candidate_api_returns_only_minimum_person_fields(
    api_client, monkeypatch
):
    client, _db, _principal = api_client
    monkeypatch.setattr(
        provincial_roles,
        "list_provincial_manager_candidates",
        lambda *_args, **_kwargs: (
            provincial_roles.ProvincialManagerCandidate(
                user_id="internal-user-id",
                person_id=PERSON_ID,
                person_name="王工程师",
                employee_no="NIO-001",
                organization_id=REGION_ID,
                organization_code="JS",
                organization_name="江苏区域公司",
                authorization_version=7,
            ),
        ),
    )

    response = client.get(
        "/api/access/provincial-managers/candidates",
        params={"organization_id": str(REGION_ID)},
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "person_id": str(PERSON_ID),
            "person_name": "王工程师",
            "employee_no": "NIO-001",
            "organization_id": str(REGION_ID),
            "organization_code": "JS",
            "organization_name": "江苏区域公司",
            "authorization_version": 7,
        }
    ]
    assert "user_id" not in response.text


def test_region_api_returns_only_active_scope_coordinates(api_client, monkeypatch):
    client, _db, _principal = api_client
    monkeypatch.setattr(
        provincial_roles,
        "list_provincial_manager_regions",
        lambda *_args, **_kwargs: (
            provincial_roles.ProvincialRegionOption(
                organization_id=REGION_ID,
                organization_code="JS",
                organization_name="江苏区域公司",
                province_code="320000",
            ),
        ),
    )

    response = client.get("/api/access/provincial-managers/regions")

    assert response.status_code == 200
    assert response.json() == [
        {
            "organization_id": str(REGION_ID),
            "organization_code": "JS",
            "organization_name": "江苏区域公司",
            "province_code": "320000",
        }
    ]


def test_assignment_list_api_does_not_expose_login_identity(
    api_client, monkeypatch
):
    client, _db, _principal = api_client
    monkeypatch.setattr(
        provincial_roles,
        "list_provincial_manager_assignments",
        lambda *_args, **_kwargs: (
            provincial_roles.ProvincialManagerAssignmentView(
                assignment_id=ASSIGNMENT_ID,
                person_id=PERSON_ID,
                person_name="王工程师",
                employee_no="NIO-001",
                organization_id=REGION_ID,
                organization_code="JS",
                organization_name="江苏区域公司",
                valid_from=NOW,
                valid_to=None,
                status="active",
                authorization_version=8,
            ),
        ),
    )

    response = client.get(
        "/api/access/provincial-managers/assignments",
        params={"organization_id": str(REGION_ID)},
    )

    assert response.status_code == 200
    assert response.json()[0]["assignment_id"] == str(ASSIGNMENT_ID)
    assert "user_id" not in response.text
    assert "mobile" not in response.text
    assert "identifier_hash" not in response.text


def test_grant_api_commits_once_and_returns_auditable_result(
    api_client, monkeypatch
):
    client, db, _principal = api_client
    captured: dict[str, object] = {}

    def fake_grant(_db, **kwargs):
        captured.update(kwargs)
        return _mutation_result()

    monkeypatch.setattr(
        provincial_roles,
        "grant_provincial_manager",
        fake_grant,
    )

    response = client.post(
        "/api/access/provincial-managers/assignments",
        json=_grant_payload(),
        headers=_headers(),
    )

    assert response.status_code == 201
    assert response.headers["Idempotency-Replayed"] == "false"
    assert response.json()["person_id"] == str(PERSON_ID)
    assert response.json()["audit_event_id"]
    assert response.json()["state_transition_event_id"]
    assert "target_user_id" not in response.text
    assert captured["target_person_id"] == PERSON_ID
    assert captured["idempotency_key"] == "provincial-grant-0001"
    assert captured["request_id"] == "request-0001"
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


def test_replayed_grant_is_explicit_in_body_and_header(api_client, monkeypatch):
    client, db, _principal = api_client
    monkeypatch.setattr(
        provincial_roles,
        "grant_provincial_manager",
        lambda *_args, **_kwargs: _mutation_result(replayed=True),
    )

    response = client.post(
        "/api/access/provincial-managers/assignments",
        json=_grant_payload(),
        headers=_headers(**{"X-Request-ID": "request-retry-0002"}),
    )

    assert response.status_code == 201
    assert response.headers["Idempotency-Replayed"] == "true"
    assert response.json()["replayed"] is True
    db.commit.assert_called_once_with()


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Request-ID": "request-0001"},
        {"Idempotency-Key": "provincial-grant-0001"},
        {
            "Idempotency-Key": "contains unsafe space",
            "X-Request-ID": "request-0001",
        },
    ],
)
def test_write_api_rejects_missing_or_unsafe_trace_headers(
    api_client, monkeypatch, headers
):
    client, db, _principal = api_client
    service = Mock()
    monkeypatch.setattr(
        provincial_roles,
        "grant_provincial_manager",
        service,
    )

    response = client.post(
        "/api/access/provincial-managers/assignments",
        json=_grant_payload(),
        headers=headers,
    )

    assert response.status_code == 400
    service.assert_not_called()
    db.commit.assert_not_called()


def test_write_payload_rejects_client_supplied_role_or_scope(api_client):
    client, db, _principal = api_client

    response = client.post(
        "/api/access/provincial-managers/assignments",
        json=_grant_payload(role_code="admin", scope_type="national"),
        headers=_headers(),
    )

    assert response.status_code == 422
    db.commit.assert_not_called()


def test_domain_error_is_sanitized_and_transaction_is_rolled_back(
    api_client, monkeypatch
):
    client, db, _principal = api_client

    def fail(*_args, **_kwargs):
        raise provincial_roles.ProvincialRoleAssignmentError(
            "authorization_version_mismatch",
            "precondition_failed",
            "目标账号授权版本已变化，请重新读取后再操作",
        )

    monkeypatch.setattr(
        provincial_roles,
        "grant_provincial_manager",
        fail,
    )

    response = client.post(
        "/api/access/provincial-managers/assignments",
        json=_grant_payload(),
        headers=_headers(),
    )

    assert response.status_code == 412
    assert response.json()["detail"]["code"] == "authorization_version_mismatch"
    assert "sql" not in response.text.lower()
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


def test_revoke_api_uses_fixed_assignment_path_and_commits(api_client, monkeypatch):
    client, db, _principal = api_client
    captured: dict[str, object] = {}

    def fake_revoke(_db, **kwargs):
        captured.update(kwargs)
        return _mutation_result(status="revoked")

    monkeypatch.setattr(
        provincial_roles,
        "revoke_provincial_manager",
        fake_revoke,
    )

    response = client.post(
        f"/api/access/provincial-managers/assignments/{ASSIGNMENT_ID}/revoke",
        json={
            "expected_authorization_version": 8,
            "reason": "人员调整，管理员手工撤销",
        },
        headers=_headers(
            **{
                "Idempotency-Key": "provincial-revoke-0001",
                "X-Request-ID": "request-revoke-0001",
            }
        ),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "revoked"
    assert captured["assignment_id"] == ASSIGNMENT_ID
    assert "valid_to" not in captured
    db.commit.assert_called_once_with()


def test_permission_dependency_denies_before_candidate_service(
    api_client, monkeypatch
):
    client, _db, principal = api_client
    principal.allowed = False
    service = Mock()
    monkeypatch.setattr(
        provincial_roles,
        "list_provincial_manager_candidates",
        service,
    )

    response = client.get(
        "/api/access/provincial-managers/candidates",
        params={"organization_id": str(REGION_ID)},
    )

    assert response.status_code == 403
    service.assert_not_called()
