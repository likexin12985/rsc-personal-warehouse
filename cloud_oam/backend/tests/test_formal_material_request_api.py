from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app import main as main_module
from app.config import Settings, get_settings
from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_services import material_request_approval as approval_service
from app.formal_services import material_request_draft as draft_service
from app.formal_services import material_request_edit as edit_service
from app.formal_services import material_request_lifecycle as lifecycle_service
from app.formal_services import material_request_query as query_service
from app.formal_services import material_request_supply as supply_service
from app.formal_services.material_request_policy import ApprovalLineDecision
from app.material_request_read_schemas import MaterialRequestPageOut
from app.routers import formal_material_requests


REQUEST_ID = uuid.UUID("a1000000-0000-4000-8000-000000000001")
REVISION_ID = uuid.UUID("a1000000-0000-4000-8000-000000000002")
INSTANCE_ID = uuid.UUID("a1000000-0000-4000-8000-000000000003")
STEP_1_ID = uuid.UUID("a1000000-0000-4000-8000-000000000004")
STEP_2_ID = uuid.UUID("a1000000-0000-4000-8000-000000000005")
STEP_3_ID = uuid.UUID("a1000000-0000-4000-8000-000000000006")
REGISTRATION_ID = uuid.UUID("a1000000-0000-4000-8000-000000000007")
LINE_ID = uuid.UUID("a1000000-0000-4000-8000-000000000008")
MATERIAL_ID = uuid.UUID("a1000000-0000-4000-8000-000000000009")
PERSON_ID = uuid.UUID("a1000000-0000-4000-8000-00000000000a")
FILE_ID = uuid.UUID("a1000000-0000-4000-8000-00000000000b")
WORK_ORDER_ID = uuid.UUID("a1000000-0000-4000-8000-00000000000c")

IDEMPOTENCY_SECRET = "demand-idempotency-secret-that-is-at-least-32-chars"
MOBILE_SECRET = "demand-mobile-hash-secret-that-is-at-least-32-chars"


class _Db:
    def __init__(self) -> None:
        self.commit = Mock()
        self.rollback = Mock()
        self.get = Mock(return_value=SimpleNamespace(attempt_no=1))


class _Principal:
    def __init__(self, user_id: str = "user-001") -> None:
        self.user_id = user_id
        self.person_id = PERSON_ID
        self.calls: list[tuple[str, str, str]] = []
        self.permissions = {
            ("material_request", "read", ""),
            ("material_request", "create", ""),
            ("material_request", "update_draft", ""),
            ("material_request", "submit", ""),
            ("material_request", "withdraw", ""),
            ("material_request", "cancel", ""),
            ("material_request", "approve_region", "approval_decision"),
            ("material_request", "approve_headquarters", "approval_decision"),
            ("material_request", "register_external", "approval_evidence"),
            ("material_request", "verify_external", "approval_evidence"),
            ("supply_task", "manage", ""),
        }

    def allows(
        self,
        _db,
        resource: str,
        action: str,
        *,
        field_code: str = "",
        **_kwargs,
    ) -> bool:
        self.calls.append((resource, action, field_code))
        return (resource, action, field_code) in self.permissions


class _FakeCipher:
    def __init__(self) -> None:
        self.plaintexts: list[bytes] = []
        self.aads: list[bytes] = []
        self.version_calls = 0

    def active_key_version(self) -> int:
        self.version_calls += 1
        return 7

    def encrypt(self, plaintext: bytes, *, aad: bytes, key_version: int):
        self.plaintexts.append(plaintext)
        self.aads.append(aad)
        return SimpleNamespace(
            ciphertext=b"c" * 32,
            nonce=b"n" * 12,
            key_version=key_version,
        )

    def decrypt(
        self,
        _ciphertext: bytes,
        *,
        nonce: bytes,
        aad: bytes,
        key_version: int,
    ) -> bytes:
        assert nonce == b"n" * 12
        assert key_version == 7
        index = self.aads.index(aad)
        return self.plaintexts[index]


def _settings(*, writes_enabled: bool = True) -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+pysqlite:///:memory:",
        database_schema_mode="alembic",
        material_request_writes_enabled=writes_enabled,
        material_request_idempotency_hmac_secret=IDEMPOTENCY_SECRET,
        material_request_contact_mobile_hmac_secret=MOBILE_SECRET,
        material_request_contact_mobile_hash_version=3,
        material_request_contact_encryption_provider="aliyun_kms",
        material_request_contact_kms_key_id="kms-material-request-contact-test",
        auth_idempotency_kms_key_id="kms-auth-test-distinct",
    )


@pytest.fixture()
def api_client():
    db = _Db()
    principal_box = {"value": _Principal()}
    cipher = _FakeCipher()
    settings = _settings()
    api = FastAPI()
    formal_material_requests.install_formal_material_request_validation_exception_handler(
        api
    )
    api.include_router(formal_material_requests.router, prefix="/api")
    api.include_router(formal_material_requests.command_status_router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_formal_principal] = (
        lambda: principal_box["value"]
    )
    api.dependency_overrides[get_settings] = lambda: settings
    api.dependency_overrides[
        formal_material_requests.get_material_request_contact_cipher
    ] = lambda: cipher
    with TestClient(api) as client:
        yield client, db, principal_box, cipher, settings


def _headers(suffix: str = "0001") -> dict[str, str]:
    return {
        "Idempotency-Key": f"material-request-key-{suffix}",
        "X-Request-ID": f"material-request-trace-{suffix}",
    }


def _draft_body(*, expected_version: int | None = None) -> dict[str, object]:
    body: dict[str, object] = {
        "work_order_id": None,
        "purpose": "现场维修补充物料",
        "urgency": "normal",
        "expected_date": "2026-09-10",
        "address": {
            "province_code": "JS",
            "province_name": "江苏省",
            "city_name": "南京市",
            "district_name": "建邺区",
            "detail": "江东中路 1 号 8 楼",
        },
        "contact": {"name": "王小明", "mobile": "138 0000 1234"},
        "attachment_file_ids": [],
        "note": "仅用于该需求",
        "lines": [
            {
                "material_id": str(MATERIAL_ID),
                "requested_qty": "2.500",
                "required_date": "2026-09-10",
                "suggested_substitute_material_id": None,
                "note": "原物料",
            }
        ],
    }
    if expected_version is not None:
        body["expected_version"] = expected_version
    return body


def _approval_body(*, approved_qty: str = "2.000") -> dict[str, object]:
    return {
        "expected_request_version": 2,
        "expected_step_version": 0,
        "action": "approve",
        "lines": [
            {
                "request_line_id": str(LINE_ID),
                "approved_qty": approved_qty,
                "reason": "同意数量",
            }
        ],
        "return_lines": [],
        "comment": "",
    }


def _external_body() -> dict[str, object]:
    return {
        "expected_request_version": 4,
        "expected_step_version": 0,
        "evidence_file_id": str(FILE_ID),
        "external_approver_name": "星星审批人甲",
        "external_reference_no": "STAR-APPROVAL-0001",
        "external_decided_at": "2026-09-01T08:00:00Z",
        "action": "approve",
        "lines": [
            {
                "request_line_id": str(LINE_ID),
                "approved_qty": "1.500",
                "reason": "外部部分同意",
            }
        ],
        "return_lines": [],
        "comment": "登记外部审批证据",
    }


def _states(request_status: str) -> dict[str, str]:
    return {
        "request_status": request_status,
        "allocation_status": "not_allocated",
        "reservation_status": "not_reserved",
        "outbound_status": "not_started",
        "shipment_status": "not_started",
        "logistics_signature_status": "not_signed",
        "oam_receipt_status": "not_occurred",
        "personal_inbound_status": "not_started",
        "notification_status": "not_started",
        "reconciliation_status": "not_started",
    }


def _lifecycle_result(action: str):
    return lifecycle_service.MaterialRequestLifecycleResult(
        request_id=REQUEST_ID,
        request_no="MR-20260901-0001",
        action=action,
        request_status="withdrawn" if action == "withdraw" else "cancelled",
        request_version=7,
        revision_id=REVISION_ID,
        revision_no=1,
        approval_instance_id=INSTANCE_ID,
        approval_attempt_no=1,
        current_step_id=None,
        state_axes={
            key: value
            for key, value in _states("draft").items()
            if key != "request_status"
        },
        replayed=False,
    )


def _create_result(*, replayed: bool = False):
    return SimpleNamespace(
        request_id=REQUEST_ID,
        request_version=0,
        revision_id=REVISION_ID,
        revision_no=1,
        state_axes=_states("draft"),
        idempotency_replayed=replayed,
    )


def _approval_result(
    *,
    decided_step_id: uuid.UUID,
    current_step_id: uuid.UUID | None,
    request_version: int,
    request_status: str = "approval_in_progress",
):
    return SimpleNamespace(
        request_id=REQUEST_ID,
        request_version=request_version,
        revision_id=REVISION_ID,
        revision_no=1,
        instance_id=INSTANCE_ID,
        decided_step_id=decided_step_id,
        current_step_id=current_step_id,
        state_axes=_states(request_status),
        replayed=False,
    )


def test_list_is_read_only_stable_pagination_and_works_with_writes_disabled(
    api_client,
    monkeypatch,
):
    client, db, principal_box, _cipher, settings = api_client
    settings.material_request_writes_enabled = False
    captured: dict[str, object] = {}

    def fake_list(_db, **kwargs):
        captured.update(kwargs)
        return MaterialRequestPageOut(items=(), next_after_id=None)

    monkeypatch.setattr(query_service, "list_material_requests", fake_list)
    after_id = uuid.uuid4()
    response = client.get(
        "/api/v1/material-requests",
        params={"limit": 25, "after_id": str(after_id)},
    )

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": "1.0",
        "items": [],
        "next_after_id": None,
    }
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert captured == {
        "actor": principal_box["value"],
        "limit": 25,
        "after_id": after_id,
    }
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_detail_preserves_scope_safe_404(api_client, monkeypatch):
    client, db, _principal_box, _cipher, _settings_value = api_client

    def hidden(*_args, **_kwargs):
        raise query_service.MaterialRequestReadError(
            "material_request_not_found",
            "not_found",
            "需求单不存在",
        )

    monkeypatch.setattr(query_service, "material_request_detail", hidden)
    response = client.get(f"/api/v1/material-requests/{REQUEST_ID}")

    assert response.status_code == 404
    assert response.json()["detail"] == {
        "code": "material_request_not_found",
        "category": "not_found",
        "message": "需求单不存在",
    }
    db.commit.assert_not_called()


def test_editable_draft_is_owner_only_no_store_and_read_only(
    api_client,
    monkeypatch,
):
    client, db, principal_box, cipher, _settings_value = api_client
    captured: dict[str, object] = {}

    def fake_edit(_db, **kwargs):
        captured.update(kwargs)
        return edit_service.MaterialRequestEditableDraftOut(
            request_id=REQUEST_ID,
            request_version=0,
            draft=_draft_body(),
        )

    monkeypatch.setattr(edit_service, "material_request_editable_draft", fake_edit)
    response = client.get(
        f"/api/v1/material-requests/{REQUEST_ID}/editable-draft"
    )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store, max-age=0"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    body = response.json()
    assert body["schema_version"] == "1.0"
    assert body["request_id"] == str(REQUEST_ID)
    assert body["request_version"] == 0
    assert body["draft"]["contact"] == {
        "name": "王小明",
        "mobile": "138 0000 1234",
    }
    assert captured["actor"] is principal_box["value"]
    assert captured["request_id"] == REQUEST_ID
    assert captured["cipher"] is cipher
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_editable_draft_permission_or_runtime_failure_discloses_no_plaintext(
    api_client,
    monkeypatch,
):
    client, db, principal_box, cipher, settings = api_client
    service = Mock()
    monkeypatch.setattr(edit_service, "material_request_editable_draft", service)
    principal_box["value"].permissions.remove(
        ("material_request", "update_draft", "")
    )

    denied = client.get(
        f"/api/v1/material-requests/{REQUEST_ID}/editable-draft"
    )
    assert denied.status_code == 403
    service.assert_not_called()
    assert cipher.version_calls == 0

    principal_box["value"].permissions.add(
        ("material_request", "update_draft", "")
    )
    settings.material_request_writes_enabled = False
    unavailable = client.get(
        f"/api/v1/material-requests/{REQUEST_ID}/editable-draft"
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == (
        "material_request_writes_disabled"
    )
    service.assert_not_called()
    assert cipher.version_calls == 0
    db.commit.assert_not_called()


def test_create_protects_plaintext_before_domain_service_and_never_returns_pii(
    api_client,
    monkeypatch,
    caplog,
):
    client, db, principal_box, cipher, _settings_value = api_client
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        draft_service,
        "derive_material_request_create_id",
        lambda **_kwargs: REQUEST_ID,
    )

    def fake_create(_db, **kwargs):
        captured.update(kwargs)
        return _create_result()

    monkeypatch.setattr(draft_service, "create_material_request_draft", fake_create)
    body = _draft_body()
    body["work_order_id"] = str(WORK_ORDER_ID)
    response = client.post(
        "/api/v1/material-requests",
        json=body,
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.headers["Idempotency-Replayed"] == "false"
    response_text = json.dumps(response.json(), ensure_ascii=False)
    assert "王小明" not in response_text
    assert "138 0000 1234" not in response_text
    assert "江东中路" not in response_text
    draft = captured["draft"]
    assert isinstance(draft, draft_service.MaterialRequestDraftInput)
    assert draft.work_order_id == WORK_ORDER_ID
    assert draft.contact_masked == {
        "name_masked": "王**",
        "mobile_masked": "*******1234",
    }
    persisted_shape = json.dumps(
        {
            "contact_envelope": draft.contact_envelope,
            "contact_masked": draft.contact_masked,
        },
        ensure_ascii=False,
    )
    assert "王小明" not in persisted_shape
    assert "13800001234" not in persisted_shape
    plaintext = json.loads(cipher.plaintexts[0])
    assert plaintext == {"mobile": "13800001234", "name": "王小明"}
    assert f"request_id={REQUEST_ID}".encode() in cipher.aads[0]
    assert f"requester_person_id={PERSON_ID}".encode() in cipher.aads[0]
    assert captured["actor"] is principal_box["value"]
    assert captured["idempotency_hmac_secret"] == IDEMPOTENCY_SECRET
    assert all("王小明" not in record.getMessage() for record in caplog.records)
    assert all("13800001234" not in record.getMessage() for record in caplog.records)
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


def test_request_validation_response_never_echoes_plaintext_contact(
    api_client,
    monkeypatch,
):
    client, db, _principal_box, cipher, _settings_value = api_client
    service = Mock()
    monkeypatch.setattr(draft_service, "create_material_request_draft", service)
    body = _draft_body()
    body["lines"][0]["requested_qty"] = 2.5

    response = client.post(
        "/api/v1/material-requests",
        json=body,
        headers=_headers("pii-invalid"),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "material_request_request_invalid",
        "category": "invalid_request",
        "message": "需求单请求字段无效",
    }
    response_text = json.dumps(response.json(), ensure_ascii=False)
    assert "王小明" not in response_text
    assert "138 0000 1234" not in response_text
    assert "江东中路" not in response_text
    service.assert_not_called()
    assert cipher.version_calls == 0
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_replace_is_full_put_and_reprotects_contact_for_exact_path_id(
    api_client,
    monkeypatch,
):
    client, db, _principal_box, cipher, _settings_value = api_client
    captured: dict[str, object] = {}

    def fake_amend(_db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            request_id=REQUEST_ID,
            version=1,
            revision_id=REVISION_ID,
            revision_no=1,
            state_axes=_states("draft"),
            replayed=True,
        )

    monkeypatch.setattr(draft_service, "amend_material_request_draft", fake_amend)
    response = client.put(
        f"/api/v1/material-requests/{REQUEST_ID}",
        json=_draft_body(expected_version=0),
        headers=_headers("0002"),
    )

    assert response.status_code == 200
    assert response.headers["Idempotency-Replayed"] == "true"
    assert captured["material_request_id"] == REQUEST_ID
    assert captured["expected_version"] == 0
    assert f"request_id={REQUEST_ID}".encode() in cipher.aads[0]
    assert response.json()["action"] == "update"
    db.commit.assert_called_once_with()


def test_submit_maps_frozen_three_stage_coordinates_without_advancing_other_axes(
    api_client,
    monkeypatch,
):
    client, db, _principal_box, _cipher, _settings_value = api_client
    captured: dict[str, object] = {}

    def fake_submit(_db, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            request_id=REQUEST_ID,
            version=1,
            revision_id=REVISION_ID,
            revision_no=1,
            approval_attempt_no=1,
            approval_instance_id=INSTANCE_ID,
            approval_step_ids=(STEP_1_ID, STEP_2_ID, STEP_3_ID),
            state_axes=_states("submitted"),
            replayed=False,
        )

    monkeypatch.setattr(draft_service, "submit_material_request", fake_submit)
    response = client.post(
        f"/api/v1/material-requests/{REQUEST_ID}/submit",
        json={"expected_version": 0},
        headers=_headers("0003"),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["approval_instance_id"] == str(INSTANCE_ID)
    assert body["current_step_id"] == str(STEP_1_ID)
    assert body["states"] == _states("submitted")
    assert captured["actor"] is not None
    assert captured["idempotency_hmac_secret"] == IDEMPOTENCY_SECRET
    db.commit.assert_called_once_with()


def test_internal_approval_maps_both_levels_and_exact_line_decisions(
    api_client,
    monkeypatch,
):
    client, db, _principal_box, _cipher, _settings_value = api_client
    calls: list[dict[str, object]] = []
    results = iter(
        (
            _approval_result(
                decided_step_id=STEP_1_ID,
                current_step_id=STEP_2_ID,
                request_version=3,
            ),
            _approval_result(
                decided_step_id=STEP_2_ID,
                current_step_id=STEP_3_ID,
                request_version=4,
            ),
        )
    )

    def fake_decide(_db, **kwargs):
        calls.append(kwargs)
        return next(results)

    monkeypatch.setattr(
        approval_service,
        "decide_material_request_approval",
        fake_decide,
    )
    first = client.post(
        f"/api/v1/material-requests/{REQUEST_ID}/approval-steps/{STEP_1_ID}/decision",
        json=_approval_body(approved_qty="2.000"),
        headers=_headers("1001"),
    )
    second = client.post(
        f"/api/v1/material-requests/{REQUEST_ID}/approval-steps/{STEP_2_ID}/decision",
        json=_approval_body(approved_qty="1.500"),
        headers=_headers("1002"),
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["current_step_id"] == str(STEP_2_ID)
    assert second.json()["current_step_id"] == str(STEP_3_ID)
    assert [call["approval_step_id"] for call in calls] == [STEP_1_ID, STEP_2_ID]
    assert calls[0]["decision"].lines == (
        ApprovalLineDecision(
            request_line_id=LINE_ID,
            approved_qty=Decimal("2.000"),
            reason="同意数量",
        ),
    )
    assert calls[1]["decision"].lines[0].approved_qty == Decimal("1.500")
    assert db.commit.call_count == 2
    db.rollback.assert_not_called()


def test_external_registration_and_distinct_verifier_are_mapped_as_two_commands(
    api_client,
    monkeypatch,
):
    client, db, principal_box, _cipher, _settings_value = api_client
    captured: dict[str, dict[str, object]] = {}

    def fake_register(_db, **kwargs):
        captured["register"] = kwargs
        return SimpleNamespace(
            request_id=REQUEST_ID,
            request_version=5,
            revision_id=REVISION_ID,
            revision_no=1,
            instance_id=INSTANCE_ID,
            step_id=STEP_3_ID,
            state_axes=_states("approval_in_progress"),
            replayed=False,
        )

    def fake_verify(_db, **kwargs):
        captured["verify"] = kwargs
        return SimpleNamespace(
            request_id=REQUEST_ID,
            request_version=6,
            revision_id=REVISION_ID,
            revision_no=1,
            instance_id=INSTANCE_ID,
            current_step_id=None,
            state_axes=_states("partially_approved"),
            replayed=False,
        )

    monkeypatch.setattr(
        approval_service,
        "register_external_approval_evidence",
        fake_register,
    )
    monkeypatch.setattr(
        approval_service,
        "verify_external_approval_evidence",
        fake_verify,
    )
    registrar = principal_box["value"]
    registered = client.post(
        f"/api/v1/material-requests/{REQUEST_ID}/approval-steps/{STEP_3_ID}/external-evidence",
        json=_external_body(),
        headers=_headers("2001"),
    )
    verifier = _Principal("user-002")
    principal_box["value"] = verifier
    verified = client.post(
        f"/api/v1/material-requests/{REQUEST_ID}/approval-steps/{STEP_3_ID}/external-evidence/{REGISTRATION_ID}/verification",
        json={
            "expected_request_version": 5,
            "expected_step_version": 1,
            "decision": "accept",
            "comment": "双人复核通过",
        },
        headers=_headers("2002"),
    )

    assert registered.status_code == verified.status_code == 200
    assert captured["register"]["actor"] is registrar
    assert captured["verify"]["actor"] is verifier
    assert captured["verify"]["registration_id"] == REGISTRATION_ID
    registration = captured["register"]["registration"]
    assert registration.external_approver_name == "星星审批人甲"
    assert registration.lines[0].approved_qty == Decimal("1.500")
    assert "星星审批人甲" not in json.dumps(registered.json(), ensure_ascii=False)
    assert "STAR-APPROVAL-0001" not in json.dumps(
        registered.json(), ensure_ascii=False
    )
    assert verified.json()["states"] == _states("partially_approved")
    assert db.commit.call_count == 2


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/v1/material-requests", _draft_body()),
        (
            "put",
            f"/api/v1/material-requests/{REQUEST_ID}",
            _draft_body(expected_version=0),
        ),
        (
            "post",
            f"/api/v1/material-requests/{REQUEST_ID}/submit",
            {"expected_version": 0},
        ),
        (
            "post",
            f"/api/v1/material-requests/{REQUEST_ID}/approval-steps/{STEP_1_ID}/decision",
            _approval_body(),
        ),
        (
            "post",
            f"/api/v1/material-requests/{REQUEST_ID}/approval-steps/{STEP_3_ID}/external-evidence",
            _external_body(),
        ),
        (
            "post",
            f"/api/v1/material-requests/{REQUEST_ID}/approval-steps/{STEP_3_ID}/external-evidence/{REGISTRATION_ID}/verification",
            {
                "expected_request_version": 5,
                "expected_step_version": 1,
                "decision": "accept",
                "comment": "通过",
            },
        ),
    ],
)
def test_every_write_requires_both_safe_headers(api_client, method, path, body):
    client, db, _principal_box, cipher, _settings_value = api_client

    response = client.request(
        method,
        path,
        json=body,
        headers={"X-Request-ID": "request-only-0001"},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "idempotency_key_invalid"
    assert cipher.version_calls == 0
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_permission_denial_happens_before_write_runtime_or_service(
    api_client,
    monkeypatch,
):
    client, db, principal_box, cipher, _settings_value = api_client
    principal_box["value"].permissions.clear()
    service = Mock()
    monkeypatch.setattr(draft_service, "submit_material_request", service)

    response = client.post(
        f"/api/v1/material-requests/{REQUEST_ID}/submit",
        json={"expected_version": 0},
        headers=_headers("3001"),
    )

    assert response.status_code == 403
    service.assert_not_called()
    assert cipher.version_calls == 0
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_missing_default_kms_dependency_fails_closed_with_zero_domain_write(
    monkeypatch,
):
    db = _Db()
    principal = _Principal()
    settings = _settings()
    service = Mock()
    monkeypatch.setattr(draft_service, "submit_material_request", service)
    api = FastAPI()
    formal_material_requests.install_formal_material_request_validation_exception_handler(
        api
    )
    api.include_router(formal_material_requests.router, prefix="/api")
    api.dependency_overrides[get_db] = lambda: db
    api.dependency_overrides[get_formal_principal] = lambda: principal
    api.dependency_overrides[get_settings] = lambda: settings

    with TestClient(api) as client:
        response = client.post(
            f"/api/v1/material-requests/{REQUEST_ID}/submit",
            json={"expected_version": 0},
            headers=_headers("4001"),
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == (
        "material_request_contact_kms_unavailable"
    )
    service.assert_not_called()
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


def test_write_feature_off_fails_closed_but_does_not_call_cipher_or_service(
    api_client,
    monkeypatch,
):
    client, db, _principal_box, cipher, settings = api_client
    settings.material_request_writes_enabled = False
    service = Mock()
    monkeypatch.setattr(draft_service, "submit_material_request", service)

    response = client.post(
        f"/api/v1/material-requests/{REQUEST_ID}/submit",
        json={"expected_version": 0},
        headers=_headers("4002"),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "material_request_writes_disabled"
    service.assert_not_called()
    assert cipher.version_calls == 0
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()


def test_domain_and_database_errors_are_rolled_back_with_stable_details(
    api_client,
    monkeypatch,
):
    client, db, _principal_box, _cipher, _settings_value = api_client
    failures = iter(
        (
            draft_service.MaterialRequestDraftError(
                "material_request_version_conflict",
                "conflict",
                "需求单版本已变化",
            ),
            OperationalError("hidden statement", {}, RuntimeError("secret-db-detail")),
        )
    )

    def fail(*_args, **_kwargs):
        raise next(failures)

    monkeypatch.setattr(draft_service, "submit_material_request", fail)
    domain = client.post(
        f"/api/v1/material-requests/{REQUEST_ID}/submit",
        json={"expected_version": 0},
        headers=_headers("5001"),
    )
    database = client.post(
        f"/api/v1/material-requests/{REQUEST_ID}/submit",
        json={"expected_version": 0},
        headers=_headers("5002"),
    )

    assert domain.status_code == 409
    assert domain.json()["detail"]["code"] == "material_request_version_conflict"
    assert database.status_code == 503
    database_text = json.dumps(database.json(), ensure_ascii=False)
    assert "secret-db-detail" not in database_text
    assert "hidden statement" not in database_text
    assert db.rollback.call_count == 2
    db.commit.assert_not_called()


def test_all_formal_routes_are_mounted_outside_legacy_nonproduction_block():
    source = inspect.getsource(main_module)
    formal_mount = 'app.include_router(formal_material_requests.router, prefix="/api")'
    legacy_gate = 'if settings.environment != "production":'
    assert formal_mount in source
    assert source.index(formal_mount) < source.index(legacy_gate)
    paths = {
        route.path
        for route in main_module.app.routes
        if "material-requests" in route.path
    }
    assert "/api/v1/material-requests" in paths
    assert (
        "/api/v1/material-requests/{material_request_id}/approval-steps/"
        "{approval_step_id}/external-evidence/{registration_id}/verification"
    ) in paths
    assert "/api/stocktakes" in main_module.LEGACY_PROTOTYPE_WRITE_PREFIXES
    assert "/api/v1/material-requests" not in (
        main_module.LEGACY_PROTOTYPE_WRITE_PREFIXES
    )


def _production_settings(**overrides) -> Settings:
    values = {
        "environment": "production",
        "database_url": "postgresql+psycopg://star_oam_api:test@db/test",
        "database_schema_mode": "alembic",
        "legacy_prototype_writes_enabled": False,
        "password_login_enabled": False,
        "admin_mobile": "",
        "admin_name": "",
        "admin_initial_password": None,
        "edge_sync_enabled": False,
        "edge_sync_secret": "",
        "edge_sync_allowed_sources": "",
        "edge_sync_legacy_batches_enabled": False,
        "edge_sync_legacy_personnel_projection_enabled": False,
        "jwt_secret": "production-jwt-secret-at-least-thirty-two-characters",
        "identity_hash_secret": "production-identity-secret-at-least-thirty-two",
        "auth_idempotency_hmac_secret": "production-auth-replay-secret-at-least-thirty-two",
        "auth_idempotency_encryption_provider": "aliyun_kms",
        "auth_idempotency_kms_key_id": "kms-production-auth",
        "auth_login_rate_limit_hmac_secret": "production-login-limit-secret-at-least-thirty-two",
        "sms_login_enabled": True,
        "sms_provider": "aliyun_pnvs",
        "sms_access_key_id": "test-access-id",
        "sms_access_key_secret": "test-access-secret",
        "sms_sign_name": "test-sign",
        "sms_template_code": "SMS_TEST",
        "sms_scheme_name": "test-scheme",
        "wechat_login_enabled": False,
        "wechat_provider": "disabled",
        "wechat_app_id": "",
        "wechat_app_secret": "",
        "material_request_writes_enabled": True,
        "material_request_idempotency_hmac_secret": IDEMPOTENCY_SECRET,
        "material_request_contact_mobile_hmac_secret": MOBILE_SECRET,
        "material_request_contact_mobile_hash_version": 3,
        "material_request_contact_encryption_provider": "aliyun_kms",
        "material_request_contact_kms_key_id": "kms-production-material-contact",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_write_enablement_validates_independent_secrets_and_kms():
    settings = _production_settings()
    settings.validate_api_startup()
    assert settings.material_request_contact_kms_configuration_ready() is True

    with pytest.raises(ValueError, match="material-request writes require a dedicated"):
        _production_settings(
            material_request_idempotency_hmac_secret="",
        ).validate_api_startup()
    with pytest.raises(ValueError, match="aliyun_kms contact encryption"):
        _production_settings(
            material_request_contact_encryption_provider="disabled",
            material_request_contact_kms_key_id="",
        ).validate_api_startup()
    with pytest.raises(ValueError, match="KMS key IDs must be distinct"):
        _production_settings(
            material_request_contact_kms_key_id="kms-production-auth",
        ).validate_api_startup()
    with pytest.raises(ValueError, match="must be pairwise distinct"):
        _production_settings(
            material_request_idempotency_hmac_secret=(
                "production-jwt-secret-at-least-thirty-two-characters"
            ),
        ).validate_api_startup()
    with pytest.raises(ValidationError):
        _production_settings(material_request_contact_mobile_hash_version=0)


def test_fail_closed_material_request_defaults_do_not_break_production_reads():
    fields = Settings.model_fields
    assert fields["material_request_writes_enabled"].default is False
    assert fields["material_request_idempotency_hmac_secret"].default == ""
    assert fields["material_request_contact_mobile_hmac_secret"].default == ""
    assert fields["material_request_contact_encryption_provider"].default == "disabled"
    assert fields["material_request_contact_kms_key_id"].default == ""
    settings = _production_settings(
        material_request_writes_enabled=False,
        material_request_idempotency_hmac_secret="",
        material_request_contact_mobile_hmac_secret="",
        material_request_contact_encryption_provider="disabled",
        material_request_contact_kms_key_id="",
    )
    settings.validate_api_startup()


def test_deployment_surfaces_only_fail_closed_material_request_coordinates():
    root = Path(__file__).resolve().parents[2]
    env_example = (root / ".env.example").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    for name in (
        "OAM_MATERIAL_REQUEST_WRITES_ENABLED",
        "OAM_MATERIAL_REQUEST_IDEMPOTENCY_HMAC_SECRET",
        "OAM_MATERIAL_REQUEST_CONTACT_MOBILE_HMAC_SECRET",
        "OAM_MATERIAL_REQUEST_CONTACT_MOBILE_HASH_VERSION",
        "OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_PROVIDER",
        "OAM_MATERIAL_REQUEST_CONTACT_KMS_KEY_ID",
    ):
        assert name in env_example
        assert name in compose
    assert "OAM_MATERIAL_REQUEST_WRITES_ENABLED=false" in env_example
    assert "OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_PROVIDER=disabled" in env_example
    assert "MATERIAL_REQUEST_CONTACT_PLAINTEXT" not in env_example
    assert "MATERIAL_REQUEST_CONTACT_PLAINTEXT" not in compose


def test_withdraw_route_maps_exact_command_and_commits_once(api_client, monkeypatch):
    client, db, principal_box, _cipher, _settings = api_client
    captured: dict[str, object] = {}

    def service(service_db, **kwargs):
        assert service_db is db
        captured.update(kwargs)
        return _lifecycle_result("withdraw")

    monkeypatch.setattr(lifecycle_service, "withdraw_material_request", service)
    response = client.post(
        f"/api/v1/material-requests/{REQUEST_ID}/withdraw",
        json={"expected_version": 6, "reason": "审批中撤回"},
        headers=_headers("withdraw-0001"),
    )

    assert response.status_code == 200
    assert response.headers["Idempotency-Replayed"] == "false"
    assert response.json()["action"] == "withdraw"
    assert response.json()["states"] == _states("withdrawn")
    assert captured == {
        "actor": principal_box["value"],
        "material_request_id": REQUEST_ID,
        "expected_version": 6,
        "reason": "审批中撤回",
        "idempotency_key": "material-request-key-withdraw-0001",
        "idempotency_hmac_secret": IDEMPOTENCY_SECRET,
        "trace_request_id": "material-request-trace-withdraw-0001",
    }
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


def test_cancel_route_maps_strict_full_line_command(api_client, monkeypatch):
    client, db, principal_box, _cipher, _settings = api_client
    captured: dict[str, object] = {}

    def service(service_db, **kwargs):
        assert service_db is db
        captured.update(kwargs)
        return _lifecycle_result("cancel")

    monkeypatch.setattr(lifecycle_service, "cancel_material_request", service)
    response = client.post(
        f"/api/v1/material-requests/{REQUEST_ID}/cancel",
        json={
            "expected_version": 6,
            "reason": "需求不再存在",
            "lines": [
                {
                    "request_line_id": str(LINE_ID),
                    "cancelled_qty": "2.000",
                    "reason": "取消全部批准数量",
                }
            ],
        },
        headers=_headers("cancel-0001"),
    )

    assert response.status_code == 200
    assert response.json()["action"] == "cancel"
    assert response.json()["states"] == _states("cancelled")
    assert captured["actor"] is principal_box["value"]
    assert captured["material_request_id"] == REQUEST_ID
    assert captured["expected_version"] == 6
    cancellation = captured["cancellation"]
    assert isinstance(cancellation, lifecycle_service.MaterialRequestCancelInput)
    assert cancellation.reason == "需求不再存在"
    assert cancellation.lines == (
        lifecycle_service.MaterialRequestCancellationLineInput(
            request_line_id=LINE_ID,
            cancelled_qty=Decimal("2.000"),
            reason="取消全部批准数量",
        ),
    )
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


@pytest.mark.parametrize("bad_version", (True, False, "6", 6.0))
def test_lifecycle_command_expected_version_is_strict_and_value_free(
    api_client,
    monkeypatch,
    bad_version,
):
    client, db, _principal_box, _cipher, _settings = api_client
    service = Mock()
    monkeypatch.setattr(lifecycle_service, "withdraw_material_request", service)
    response = client.post(
        f"/api/v1/material-requests/{REQUEST_ID}/withdraw",
        json={"expected_version": bad_version, "reason": "严格版本"},
        headers=_headers(f"strict-{type(bad_version).__name__}"),
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": {
            "code": "material_request_request_invalid",
            "category": "invalid_request",
            "message": "需求单请求字段无效",
        }
    }
    assert str(bad_version) not in response.text
    service.assert_not_called()
    db.commit.assert_not_called()


def test_lifecycle_route_keeps_default_write_gate_closed(api_client, monkeypatch):
    client, db, _principal_box, _cipher, settings = api_client
    settings.material_request_writes_enabled = False
    service = Mock()
    monkeypatch.setattr(lifecycle_service, "withdraw_material_request", service)
    response = client.post(
        f"/api/v1/material-requests/{REQUEST_ID}/withdraw",
        json={"expected_version": 6, "reason": "默认关闭"},
        headers=_headers("gate-closed-0001"),
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "material_request_writes_disabled"
    service.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_called_once_with()


@pytest.mark.parametrize("action", ("withdraw", "cancel"))
def test_lifecycle_route_requires_its_exact_http_permission(
    api_client,
    monkeypatch,
    action,
):
    client, db, principal_box, _cipher, _settings = api_client
    principal_box["value"].permissions.discard(("material_request", action, ""))
    service = Mock()
    monkeypatch.setattr(
        lifecycle_service,
        f"{action}_material_request",
        service,
    )
    body = {"expected_version": 6, "reason": "权限测试"}
    if action == "cancel":
        body["lines"] = []
    response = client.post(
        f"/api/v1/material-requests/{REQUEST_ID}/{action}",
        json=body,
        headers=_headers(f"permission-{action}"),
    )

    assert response.status_code == 403
    service.assert_not_called()
    db.commit.assert_not_called()


SUPPLY_TASK_ID = uuid.UUID("a1000000-0000-4000-8000-00000000000d")


def _supply_result(action="create_supply_task", *, task_status="open", replayed=False):
    return SimpleNamespace(
        request_id=REQUEST_ID, request_version=8, revision_id=REVISION_ID,
        revision_no=1, approval_instance_id=INSTANCE_ID, approval_attempt_no=1,
        current_step_id=None, state_axes=_states("approved"), action=action,
        supply_task_id=SUPPLY_TASK_ID, task_no="SUP-20260905-TEST",
        task_status=task_status, task_version=0 if action == "create_supply_task" else 1,
        replayed=replayed,
    )


def _supply_body(operation="create"):
    if operation == "create":
        return {
            "expected_request_version": 7, "request_line_id": str(LINE_ID),
            "supply_type": "star_replenishment", "expected_qty": "2.500",
            "reference_no": None, "expected_date": "2026-09-20", "note": "补货计划",
        }
    return {
        "expected_request_version": 7, "expected_task_version": 0,
        "status": "cancelled" if operation == "cancel" else "reference_registered",
        "reference_no": "SUPPLY-REF-01", "expected_date": "2026-09-21",
        "comment": "不再需要该计划" if operation == "cancel" else "登记参考单号",
    }


@pytest.mark.parametrize("operation", ["create", "update", "cancel"])
def test_supply_routes_map_exact_plan_and_commit_without_contact_kms(
    api_client, monkeypatch, operation,
):
    client, db, principal_box, cipher, settings = api_client
    settings.material_request_contact_encryption_provider = "disabled"
    captured = {}
    action = f"{operation}_supply_task"
    body = _supply_body(operation)
    task_status = "open" if operation == "create" else body["status"]

    def service(service_db, **kwargs):
        assert service_db is db
        captured.update(kwargs)
        return _supply_result(action, task_status=task_status, replayed=True)

    service_name = "create_supply_task" if operation == "create" else "update_supply_task"
    monkeypatch.setattr(supply_service, service_name, service)
    path = f"/api/v1/material-requests/{REQUEST_ID}/supply-tasks"
    if operation != "create":
        path += f"/{SUPPLY_TASK_ID}"
    response = client.post(path, json=body, headers=_headers(f"supply-{operation}"))
    assert response.status_code == (201 if operation == "create" else 200)
    result = response.json()
    assert result["action"] == action
    assert result["supply_task_id"] == str(SUPPLY_TASK_ID)
    assert result["task_status"] == task_status
    assert result["states"] == _states("approved")
    assert response.headers["Idempotency-Replayed"] == "true"
    assert "no-store" in response.headers["Cache-Control"]
    assert captured["actor"] is principal_box["value"]
    assert captured["material_request_id"] == REQUEST_ID
    assert captured["expected_request_version"] == 7
    assert captured["idempotency_hmac_secret"] == IDEMPOTENCY_SECRET
    assert captured["trace_request_id"] == f"material-request-trace-supply-{operation}"
    if operation == "create":
        assert captured["plan"].request_line_id == LINE_ID
        assert captured["plan"].expected_qty == Decimal("2.500")
    else:
        assert captured["supply_task_id"] == SUPPLY_TASK_ID
        assert captured["expected_task_version"] == 0
        assert captured["update"].status == task_status
    assert cipher.version_calls == 0
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("failure", ["permission", "headers", "disabled", "body", "database", "domain", "projection"])
def test_supply_routes_fail_closed(api_client, monkeypatch, operation, failure):
    client, db, principal_box, _cipher, settings = api_client
    body = _supply_body(operation)
    service = Mock(return_value=_supply_result(f"{operation}_supply_task"))
    headers = _headers(f"supply-{operation}-{failure}")
    if failure == "permission":
        principal_box["value"].permissions.discard(("supply_task", "manage", ""))
    elif failure == "headers":
        headers.pop("Idempotency-Key")
    elif failure == "disabled":
        settings.material_request_writes_enabled = False
    elif failure == "body":
        body["personal_inbound_status"] = "posted"
    elif failure == "database":
        service.side_effect = OperationalError("secret sql", {}, Exception("secret connection"))
    elif failure == "domain":
        service.side_effect = supply_service.MaterialRequestSupplyError(
            "supply_quantity_conflict", "conflict", "计划数量超出剩余批准数量",
        )
    elif failure == "projection":
        service.return_value.task_status = "shipped"
    monkeypatch.setattr(supply_service, f"{operation}_supply_task", service)
    path = f"/api/v1/material-requests/{REQUEST_ID}/supply-tasks"
    if operation == "update":
        path += f"/{SUPPLY_TASK_ID}"
    response = client.post(path, json=body, headers=headers)
    expected = {"permission": 403, "headers": 400, "disabled": 503, "body": 422,
                "database": 503, "domain": 409, "projection": 503}
    assert response.status_code == expected[failure]
    assert "secret" not in response.text
    db.commit.assert_not_called()
    if failure in {"permission", "headers", "disabled", "body"}:
        service.assert_not_called()
    if failure in {"disabled", "database", "domain", "projection"}:
        db.rollback.assert_called_once_with()


@pytest.mark.parametrize("confirmed", [False, True])
def test_supply_command_recovery_is_read_only_no_store_and_needs_no_write_secret(api_client, monkeypatch, confirmed):
    client, db, principal_box, _cipher, settings = api_client
    settings.material_request_writes_enabled = False
    settings.material_request_idempotency_hmac_secret = ""
    service = Mock(return_value=SimpleNamespace(
        lookup_status="confirmed" if confirmed else "not_observed",
        command=_supply_result() if confirmed else None,
        occurred_at=datetime(2026, 9, 5, tzinfo=timezone.utc) if confirmed else None,
    ))
    monkeypatch.setattr(formal_material_requests.supply_status_service, "material_request_supply_command_status", service)
    response = client.get("/api/v1/material-request-supply-command-status", params={"trace_request_id": "supply-trace-original-001"})
    assert response.status_code == 200
    assert "no-store" in response.headers["Cache-Control"]
    assert response.json()["lookup_status"] == ("confirmed" if confirmed else "not_observed")
    if confirmed:
        command = response.json()["command"]
        assert command["supply_task_id"] == str(SUPPLY_TASK_ID)
        assert "occurred_at" in command
        assert not {"idempotency_replayed", "schema_version", "reference_no", "note", "idempotency_key"}.intersection(command)
    service.assert_called_once_with(db, actor=principal_box["value"], trace_request_id="supply-trace-original-001")
    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_supply_command_recovery_cannot_downgrade_broken_evidence_to_not_observed(api_client, monkeypatch):
    client, db, _principal, _cipher, _settings = api_client
    service = Mock(side_effect=supply_service.MaterialRequestSupplyError(
        "material_request_supply_evidence_invalid", "service_unavailable", "供给命令证据不完整",
    ))
    monkeypatch.setattr(formal_material_requests.supply_status_service, "material_request_supply_command_status", service)
    response = client.get("/api/v1/material-request-supply-command-status", params={"trace_request_id": "supply-trace-original-001"})
    assert response.status_code == 503
    assert "not_observed" not in response.text
    db.commit.assert_not_called()
    db.rollback.assert_not_called()
