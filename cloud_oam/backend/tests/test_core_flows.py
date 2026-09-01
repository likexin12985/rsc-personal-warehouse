import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / ".test_oam.db"
UPLOAD_PATH = ROOT / ".test_uploads"

os.environ["OAM_DATABASE_URL"] = f"sqlite+pysqlite:///{DB_PATH}"
os.environ["OAM_JWT_SECRET"] = "test-secret-with-at-least-thirty-two-characters"
os.environ["OAM_COOKIE_SECURE"] = "false"
os.environ["OAM_ADMIN_MOBILE"] = "18660255681"
os.environ["OAM_ADMIN_NAME"] = "系统管理员"
os.environ["OAM_ADMIN_INITIAL_PASSWORD"] = "Temporary-Admin-Password-2026!"
os.environ["OAM_UPLOAD_DIR"] = str(UPLOAD_PATH)
os.environ["OAM_ENVIRONMENT"] = "test"
os.environ["OAM_DATABASE_SCHEMA_MODE"] = "bootstrap_with_seed"
os.environ["OAM_LEGACY_PROTOTYPE_WRITES_ENABLED"] = "true"
os.environ["OAM_PASSWORD_LOGIN_ENABLED"] = "true"
os.environ["OAM_SMS_LOGIN_ENABLED"] = "true"
os.environ["OAM_SMS_PROVIDER"] = "mock"
os.environ["OAM_SMS_TEST_CODE"] = "246810"
os.environ["OAM_WECHAT_LOGIN_ENABLED"] = "true"
os.environ["OAM_WECHAT_PROVIDER"] = "mock"
os.environ["OAM_WECHAT_APP_ID"] = "wx-test-rsc"
os.environ["OAM_WECHAT_TEST_MOBILE"] = "18660255681"
os.environ["OAM_EDGE_SYNC_ENABLED"] = "true"
os.environ["OAM_EDGE_SYNC_SECRET"] = "edge-sync-test-secret-with-at-least-32-characters"
os.environ["OAM_EDGE_SYNC_LEGACY_BATCHES_ENABLED"] = "true"
os.environ["OAM_EDGE_SYNC_LEGACY_PERSONNEL_PROJECTION_ENABLED"] = "true"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    ExternalSyncCurrentRecord,
    ExternalSyncSnapshot,
    OamPersonnelBinding,
    User,
)
from app.security import hash_password  # noqa: E402


def edge_headers(body: bytes, source: str, batch_id: str, timestamp: int):
    timestamp_text = str(timestamp)
    message = b"\n".join(
        (
            timestamp_text.encode("ascii"),
            source.encode("utf-8"),
            batch_id.encode("utf-8"),
            body,
        )
    )
    signature = hmac.new(
        os.environ["OAM_EDGE_SYNC_SECRET"].encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-RSC-Edge-Source": source,
        "X-RSC-Edge-Timestamp": timestamp_text,
        "X-RSC-Edge-Batch": batch_id,
        "X-RSC-Edge-Signature": signature,
    }


def canonical_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def records_sha256(records: list[dict]) -> str:
    return hashlib.sha256(canonical_bytes(records)).hexdigest()


def test_inventory_transfer_and_stocktake_flow():
    DB_PATH.unlink(missing_ok=True)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert "cache-control" not in health.headers
        for identity_path in ("/api/auth/me", "/api/access/context"):
            anonymous_identity = client.get(identity_path)
            assert anonymous_identity.status_code == 401
            assert (
                anonymous_identity.headers["cache-control"]
                == "private, no-store, max-age=0"
            )
            assert anonymous_identity.headers["pragma"] == "no-cache"
            assert anonymous_identity.headers["referrer-policy"] == "no-referrer"
        options = client.get("/api/auth/login-options")
        assert options.status_code == 200
        assert options.json()["sms_enabled"] is True
        assert options.json()["password_enabled"] is True
        assert options.json()["wechat_enabled"] is True
        assert options.json()["session_ttl_days"] == 30

        requested = client.post(
            "/api/auth/sms/request", json={"mobile": "18660255681"}
        )
        assert requested.status_code == 200, requested.text
        assert "验证码" in requested.json()["message"]
        repeated = client.post(
            "/api/auth/sms/request", json={"mobile": "18660255681"}
        )
        assert repeated.status_code == 429
        wrong_code = client.post(
            "/api/auth/sms/login",
            json={"mobile": "18660255681", "code": "000000"},
        )
        assert wrong_code.status_code == 401
        sms_login = client.post(
            "/api/auth/sms/login",
            json={"mobile": "18660255681", "code": "246810"},
        )
        assert sms_login.status_code == 200, sms_login.text
        assert sms_login.json()["require_password_change"] is False
        current_identity = client.get("/api/auth/me")
        assert current_identity.status_code == 200
        assert (
            current_identity.headers["cache-control"]
            == "private, no-store, max-age=0"
        )
        current_access = client.get("/api/access/context")
        # The compatibility bootstrap admin deliberately has no formal
        # access-context grant; even this authenticated 403 must be private.
        assert current_access.status_code == 403
        assert (
            current_access.headers["cache-control"]
            == "private, no-store, max-age=0"
        )
        assert client.post("/api/auth/logout").status_code == 200

        unknown = client.post(
            "/api/auth/sms/request", json={"mobile": "13900000099"}
        )
        assert unknown.status_code == 200
        unknown_login = client.post(
            "/api/auth/sms/login",
            json={"mobile": "13900000099", "code": "246810"},
        )
        assert unknown_login.status_code == 401

        login = client.post(
            "/api/auth/login",
            json={
                "mobile": "18660255681",
                "password": "Temporary-Admin-Password-2026!",
            },
        )
        assert login.status_code == 200, login.text
        web_refresh = client.post("/api/auth/refresh")
        assert web_refresh.status_code == 200, web_refresh.text
        assert client.get("/api/auth/me").status_code == 200

        mini_login = client.post(
            "/api/auth/miniprogram/password-login",
            json={
                "mobile": "18660255681",
                "password": "Temporary-Admin-Password-2026!",
                "device_id": "test-mini-device-001",
                "device_name": "测试手机",
            },
        )
        assert mini_login.status_code == 200, mini_login.text
        token = mini_login.json()["access_token"]
        assert mini_login.json()["token_type"] == "bearer"
        assert mini_login.json()["expires_in"] > 0
        assert mini_login.json()["refresh_expires_in"] > 29 * 24 * 60 * 60
        old_refresh_token = mini_login.json()["refresh_token"]
        client.cookies.clear()
        bearer_headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/auth/me", headers=bearer_headers).status_code == 200
        assert client.get(
            "/api/inventory?mine=true", headers=bearer_headers
        ).status_code == 200
        assert client.get(
            "/api/auth/me", headers={"Authorization": "Bearer invalid"}
        ).status_code == 401

        refreshed = client.post(
            "/api/auth/miniprogram/refresh",
            json={
                "refresh_token": old_refresh_token,
                "device_id": "test-mini-device-001",
            },
        )
        assert refreshed.status_code == 200, refreshed.text
        refreshed_data = refreshed.json()
        assert refreshed_data["refresh_token"] != old_refresh_token
        assert client.post(
            "/api/auth/miniprogram/refresh",
            json={
                "refresh_token": old_refresh_token,
                "device_id": "test-mini-device-001",
            },
        ).status_code == 401

        binding_required = client.post(
            "/api/auth/miniprogram/wechat-login",
            json={
                "login_code": "wechat-user-001",
                "device_id": "wechat-device-001",
                "device_name": "微信测试手机",
            },
        )
        assert binding_required.status_code == 428
        wechat_login = client.post(
            "/api/auth/miniprogram/wechat-login",
            json={
                "login_code": "wechat-user-001",
                "phone_code": "wechat-phone-001",
                "device_id": "wechat-device-001",
                "device_name": "微信测试手机",
            },
        )
        assert wechat_login.status_code == 200, wechat_login.text
        wechat_session = wechat_login.json()
        silent_wechat_login = client.post(
            "/api/auth/miniprogram/wechat-login",
            json={
                "login_code": "wechat-user-001",
                "device_id": "wechat-device-001",
                "device_name": "微信测试手机",
            },
        )
        assert silent_wechat_login.status_code == 200, silent_wechat_login.text
        wechat_session = silent_wechat_login.json()

        login = client.post(
            "/api/auth/login",
            json={
                "mobile": "18660255681",
                "password": "Temporary-Admin-Password-2026!",
            },
        )
        assert login.status_code == 200, login.text

        active_sessions = client.get("/api/auth/sessions")
        assert active_sessions.status_code == 200, active_sessions.text
        assert any(row["client_type"] == "miniprogram" for row in active_sessions.json())
        revoked = client.post(
            f"/api/auth/sessions/{wechat_session['session_id']}/revoke"
        )
        assert revoked.status_code == 200, revoked.text
        assert client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {wechat_session['access_token']}"},
        ).status_code == 401

        materials = client.get("/api/materials?limit=500").json()
        warehouses = client.get("/api/warehouses").json()
        users = client.get("/api/auth/users").json()

        new_user = client.post(
            "/api/auth/users",
            json={
                "mobile": "13800000001",
                "name": "测试区域负责人",
                "role": "provincial_manager",
                "province": "江苏省",
                "temporary_password": "Warehouse-Test-2026!",
            },
        )
        assert new_user.status_code == 200, new_user.text
        assert new_user.json()["require_password_change"] is True

        new_warehouse = client.post(
            "/api/warehouses",
            json={
                "code": "TEST-WH",
                "name": "测试仓库",
                "province": "江苏省",
                "city": "南京市",
                "warehouse_type": "service_backpack",
                "condition_scope": "good",
            },
        )
        assert new_warehouse.status_code == 200, new_warehouse.text

        new_material = client.post(
            "/api/materials",
            json={
                "code": "TEST0001",
                "name": "测试物料",
                "specification": "测试规格",
                "category": "测试",
                "aliases": "测试别名",
                "unit": "个",
            },
        )
        assert new_material.status_code == 200, new_material.text
        new_material_data = new_material.json()

        material = next(row for row in materials if row["code"] == "ADQCPN0090")
        source, target = warehouses[0], warehouses[1]

        zero_adjustment = client.post(
            "/api/inventory/adjust",
            json={
                "warehouse_id": source["id"],
                "material_id": material["id"],
                "condition": "good",
                "delta": 0,
                "reason": "测试无效调整",
            },
        )
        assert zero_adjustment.status_code == 400

        adjusted = client.post(
            "/api/inventory/adjust",
            json={
                "warehouse_id": source["id"],
                "material_id": material["id"],
                "condition": "good",
                "delta": 10,
                "reason": "测试期初库存",
            },
        )
        assert adjusted.status_code == 200, adjusted.text
        assert adjusted.json()["after"] == 10

        created = client.post(
            "/api/transfers",
            json={
                "transfer_type": "internal",
                "source_warehouse_id": source["id"],
                "target_warehouse_id": target["id"],
                "recipient_user_id": None,
                "external_reference": "TEST-001",
                "note": "自动化测试",
                "items": [
                    {
                        "material_id": material["id"],
                        "quantity": 3,
                        "condition": "good",
                        "remark": "",
                    }
                ],
            },
        )
        assert created.status_code == 200, created.text
        transfer_id = created.json()["id"]

        dispatched = client.post(
            f"/api/transfers/{transfer_id}/dispatch",
            json={"logistics_company": "顺丰", "tracking_number": "SFTEST001"},
        )
        assert dispatched.status_code == 200, dispatched.text
        assert dispatched.json()["status"] == "dispatched"

        received = client.post(f"/api/transfers/{transfer_id}/receive")
        assert received.status_code == 200, received.text
        assert received.json()["status"] == "received"

        target_rows = client.get(f"/api/inventory?warehouse_id={target['id']}").json()
        target_balance = next(row for row in target_rows if row["material"]["code"] == "ADQCPN0090")
        assert target_balance["onHand"] == 3
        assert target_balance["inTransit"] == 0

        task = client.post(
            "/api/stocktakes",
            json={
                "warehouse_id": target["id"],
                "assignee_id": users[0]["id"],
                "deadline": None,
                "note": "测试盘点",
            },
        )
        assert task.status_code == 200, task.text
        task_id = task.json()["id"]
        detail = client.get(f"/api/stocktakes/{task_id}").json()
        assert len(detail["items"]) == 1
        item = detail["items"][0]
        counted = client.put(
            f"/api/stocktakes/{task_id}/items/{item['id']}",
            json={"counted_quantity": item["expected"], "remark": ""},
        )
        assert counted.status_code == 200, counted.text
        assert client.post(f"/api/stocktakes/{task_id}/submit").status_code == 200
        closed = client.post(f"/api/stocktakes/{task_id}/close")
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "closed"

        technician = client.post(
            "/api/auth/users",
            json={
                "mobile": "13800000002",
                "name": "测试工程师",
                "role": "technician",
                "province": "江苏省",
                "temporary_password": "Technician-Test-2026!",
            },
        )
        assert technician.status_code == 200, technician.text
        technician_id = technician.json()["id"]

        employee_warehouse = client.post(
            "/api/warehouses",
            json={
                "code": "TEST-EMPLOYEE-WH",
                "name": "测试员工库",
                "province": "江苏省",
                "city": "南京市",
                "warehouse_type": "network",
                "condition_scope": "good",
                "warehouse_level": "network",
                "ownership_type": "regular",
                "position_scope": "employee",
            },
        )
        assert employee_warehouse.status_code == 200, employee_warehouse.text
        employee_warehouse_id = employee_warehouse.json()["id"]

        bad_warehouse = client.post(
            "/api/warehouses",
            json={
                "code": "TEST-BAD-WH",
                "name": "测试坏件库",
                "province": "江苏省",
                "city": "南京市",
                "warehouse_type": "network",
                "condition_scope": "bad",
                "warehouse_level": "network",
                "ownership_type": "regular",
                "position_scope": "unrestricted",
            },
        )
        assert bad_warehouse.status_code == 200, bad_warehouse.text
        bad_warehouse_id = bad_warehouse.json()["id"]

        headquarters = next(row for row in warehouses if row["warehouse_level"] == "headquarters")
        hq_adjustment = client.post(
            "/api/inventory/adjust",
            json={
                "warehouse_id": headquarters["id"],
                "material_id": material["id"],
                "condition": "good",
                "delta": 8,
                "reason": "申请审批测试库存",
            },
        )
        assert hq_adjustment.status_code == 200, hq_adjustment.text
        hq_new_material_adjustment = client.post(
            "/api/inventory/adjust",
            json={
                "warehouse_id": headquarters["id"],
                "material_id": new_material_data["id"],
                "condition": "good",
                "delta": 1,
                "reason": "批量投料测试库存",
            },
        )
        assert hq_new_material_adjustment.status_code == 200, hq_new_material_adjustment.text

        client.post("/api/auth/logout")
        technician_login = client.post(
            "/api/auth/login",
            json={
                "mobile": "13800000002",
                "password": "Technician-Test-2026!",
            },
        )
        assert technician_login.status_code == 200, technician_login.text
        mismatched_personal_request = client.post(
            "/api/transfers",
            json={
                "transfer_type": "personal_request",
                "source_warehouse_id": None,
                "target_warehouse_id": employee_warehouse_id,
                "recipient_user_id": users[0]["id"],
                "work_order_number": "WT-PERSONAL-MISMATCH",
                "items": [
                    {
                        "material_id": material["id"],
                        "quantity": 1,
                        "condition": "good",
                        "remark": "",
                    }
                ],
            },
        )
        assert mismatched_personal_request.status_code == 400
        requested_transfer = client.post(
            "/api/transfers",
            json={
                "transfer_type": "personal_request",
                "source_warehouse_id": None,
                "target_warehouse_id": employee_warehouse_id,
                "recipient_user_id": technician_id,
                "work_order_number": "WT-PERSONAL-001",
                "external_reference": "",
                "note": "个人需求申请测试",
                "items": [
                    {
                        "material_id": material["id"],
                        "quantity": 2,
                        "condition": "good",
                        "remark": "",
                    },
                    {
                        "material_id": new_material_data["id"],
                        "quantity": 1,
                        "condition": "good",
                        "remark": "",
                    }
                ],
            },
        )
        assert requested_transfer.status_code == 200, requested_transfer.text
        assert requested_transfer.json()["status"] == "pending_approval"
        assert requested_transfer.json()["transferType"] == "personal_request"
        assert requested_transfer.json()["requester"]["id"] == technician_id
        assert requested_transfer.json()["recipient"]["id"] == technician_id
        request_id = requested_transfer.json()["id"]
        assert client.post(
            f"/api/transfers/{request_id}/approve",
            json={"source_warehouse_id": headquarters["id"]},
        ).status_code == 403

        client.post("/api/auth/logout")
        admin_login = client.post(
            "/api/auth/login",
            json={
                "mobile": "18660255681",
                "password": "Temporary-Admin-Password-2026!",
            },
        )
        assert admin_login.status_code == 200, admin_login.text
        approved = client.post(
            f"/api/transfers/{request_id}/approve",
            json={"source_warehouse_id": headquarters["id"]},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "draft"
        assert client.post(
            f"/api/transfers/{request_id}/dispatch",
            json={"logistics_company": "内部配送", "tracking_number": "TEST-PERSONAL-1"},
        ).status_code == 200

        client.post("/api/auth/logout")
        client.post(
            "/api/auth/login",
            json={
                "mobile": "13800000002",
                "password": "Technician-Test-2026!",
            },
        )
        manual_receive = client.post(f"/api/transfers/{request_id}/receive")
        assert manual_receive.status_code == 200, manual_receive.text
        personal_inventory = client.get("/api/inventory?mine=true").json()
        personal_material = next(
            row for row in personal_inventory if row["material"]["id"] == material["id"]
        )
        personal_new_material = next(
            row
            for row in personal_inventory
            if row["material"]["id"] == new_material_data["id"]
        )
        assert personal_material["onHand"] == 2
        assert personal_material["inTransit"] == 0
        assert personal_new_material["onHand"] == 1
        assert personal_new_material["inTransit"] == 0

        failed_batch = client.post(
            "/api/work-order-materials/batch",
            json={
                "work_order_number": "WT-BATCH-FAIL-001",
                "warehouse_id": employee_warehouse_id,
                "user_id": technician_id,
                "note": "整批失败测试",
                "items": [
                    {
                        "material_id": material["id"],
                        "condition": "good",
                        "quantity": 1,
                    },
                    {
                        "material_id": new_material_data["id"],
                        "condition": "good",
                        "quantity": 2,
                    },
                ],
            },
        )
        assert failed_batch.status_code == 409, failed_batch.text
        assert client.get(
            "/api/work-order-materials?q=WT-BATCH-FAIL-001&mine=true"
        ).json() == []
        personal_after_failed_batch = client.get("/api/inventory?mine=true").json()
        assert all(row["occupied"] == 0 for row in personal_after_failed_batch)

        batch = client.post(
            "/api/work-order-materials/batch",
            json={
                "work_order_number": "WT-BATCH-001",
                "warehouse_id": employee_warehouse_id,
                "user_id": technician_id,
                "note": "批量工单投料",
                "items": [
                    {
                        "material_id": material["id"],
                        "condition": "good",
                        "quantity": 1,
                    },
                    {
                        "material_id": new_material_data["id"],
                        "condition": "good",
                        "quantity": 1,
                    },
                ],
            },
        )
        assert batch.status_code == 200, batch.text
        assert len(batch.json()) == 2
        assert {row["status"] for row in batch.json()} == {"occupied"}
        duplicate_batch = client.post(
            "/api/work-order-materials/batch",
            json={
                "work_order_number": "WT-BATCH-001",
                "warehouse_id": employee_warehouse_id,
                "user_id": technician_id,
                "note": "重复批量工单投料",
                "items": [
                    {
                        "material_id": material["id"],
                        "condition": "good",
                        "quantity": 1,
                    }
                ],
            },
        )
        assert duplicate_batch.status_code == 409, duplicate_batch.text
        for row in batch.json():
            released = client.post(f"/api/work-order-materials/{row['id']}/release")
            assert released.status_code == 200, released.text
            assert released.json()["status"] == "released"
        personal_after_release = client.get("/api/inventory?mine=true").json()
        assert all(row["occupied"] == 0 for row in personal_after_release)

        occupied = client.post(
            "/api/work-order-materials",
            json={
                "work_order_number": "WT-PERSONAL-001",
                "warehouse_id": employee_warehouse_id,
                "material_id": material["id"],
                "condition": "good",
                "quantity": 1,
                "note": "工单投料",
            },
        )
        assert occupied.status_code == 200, occupied.text
        occupied_id = occupied.json()["id"]
        assert occupied.json()["status"] == "occupied"
        consumed = client.post(f"/api/work-order-materials/{occupied_id}/consume")
        assert consumed.status_code == 200, consumed.text
        recovered = client.post(
            f"/api/work-order-materials/{occupied_id}/recover",
            json={"recovery_condition": "bad", "note": "旧件回收"},
        )
        assert recovered.status_code == 200, recovered.text

        bad_return = client.post(
            "/api/transfers",
            json={
                "transfer_type": "bad_return",
                "source_warehouse_id": employee_warehouse_id,
                "target_warehouse_id": bad_warehouse_id,
                "recipient_user_id": None,
                "work_order_number": "WT-PERSONAL-001",
                "external_reference": "",
                "note": "坏件退回测试",
                "items": [
                    {
                        "material_id": material["id"],
                        "quantity": 1,
                        "condition": "bad",
                        "remark": "",
                    }
                ],
            },
        )
        assert bad_return.status_code == 200, bad_return.text
        bad_return_id = bad_return.json()["id"]

        client.post("/api/auth/logout")
        client.post(
            "/api/auth/login",
            json={
                "mobile": "18660255681",
                "password": "Temporary-Admin-Password-2026!",
            },
        )
        assert client.post(
            f"/api/transfers/{bad_return_id}/dispatch",
            json={"logistics_company": "内部配送", "tracking_number": "TEST-BAD-1"},
        ).status_code == 200
        returned = client.post(f"/api/transfers/{bad_return_id}/receive")
        assert returned.status_code == 200, returned.text
        returned_rows = client.get(
            f"/api/inventory?warehouse_id={bad_warehouse_id}&condition=bad"
        ).json()
        returned_balance = next(
            row for row in returned_rows if row["material"]["code"] == "ADQCPN0090"
        )
        assert returned_balance["onHand"] == 1


def test_edge_sync_signature_idempotency_and_staging():
    source = f"pytest-{uuid.uuid4().hex[:12]}"
    batch_id = f"inventory-{uuid.uuid4().hex}"

    def body_for(quantity: int) -> bytes:
        return json.dumps(
            {
                "source_system": "starcharge_oam",
                "entity_type": "inventory",
                "snapshot_at": "2026-08-29T00:00:00+00:00",
                "records": [
                    {
                        "business_key": "stock:test-001",
                        "source_updated_at": None,
                        "data": {
                            "warehouseCode": "WH-TEST",
                            "materialCode": "ADQCPN0090",
                            "qtyStock": quantity,
                        },
                    }
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    with TestClient(app) as client:
        body = body_for(2)
        current_time = int(time.time())
        headers = edge_headers(body, source, batch_id, current_time)

        invalid_headers = {**headers, "X-RSC-Edge-Signature": "0" * 64}
        invalid = client.post(
            "/api/integrations/oam/edge/batches",
            content=body,
            headers=invalid_headers,
        )
        assert invalid.status_code == 401

        stale_headers = edge_headers(body, source, batch_id, current_time - 3600)
        stale = client.post(
            "/api/integrations/oam/edge/batches",
            content=body,
            headers=stale_headers,
        )
        assert stale.status_code == 401

        accepted = client.post(
            "/api/integrations/oam/edge/batches",
            content=body,
            headers=headers,
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["duplicate"] is False
        assert accepted.json()["created"] == 1
        assert accepted.json()["mode"] == "staging_only"

        duplicate = client.post(
            "/api/integrations/oam/edge/batches",
            content=body,
            headers=headers,
        )
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["duplicate"] is True

        changed_body = body_for(3)
        conflict_headers = edge_headers(
            changed_body,
            source,
            batch_id,
            int(time.time()),
        )
        conflict = client.post(
            "/api/integrations/oam/edge/batches",
            content=changed_body,
            headers=conflict_headers,
        )
        assert conflict.status_code == 409

        update_batch_id = f"inventory-{uuid.uuid4().hex}"
        update_headers = edge_headers(
            changed_body,
            source,
            update_batch_id,
            int(time.time()),
        )
        updated = client.post(
            "/api/integrations/oam/edge/batches",
            content=changed_body,
            headers=update_headers,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["updated"] == 1

        login = client.post(
            "/api/auth/login",
            json={
                "mobile": "18660255681",
                "password": "Temporary-Admin-Password-2026!",
            },
        )
        assert login.status_code == 200, login.text
        status_response = client.get("/api/integrations/oam/edge/status")
        assert status_response.status_code == 200, status_response.text
        assert status_response.json()["configured"] is True
        assert status_response.json()["mode"] == "staging_only"
        assert status_response.json()["batch_count"] >= 2


def test_edge_snapshot_completion_is_atomic_and_idempotent():
    source = f"pytest-v2-{uuid.uuid4().hex[:12]}"
    snapshot_id = f"snapshot-{uuid.uuid4().hex}"
    scope_key = "warehouse:WH-TEST-V2"
    snapshot_at = "2026-08-29T01:00:00+00:00"
    record = {
        "business_key": "stock:test-v2-001",
        "source_updated_at": None,
        "data": {
            "companyId": "company-nio",
            "orgCode": "org-nio",
            "warehouseCode": "WH-TEST-V2",
            "materialCode": "ADQCPN0090",
            "qtyStock": 2,
        },
        "operation": "upsert",
    }
    batch_payload = {
        "source_system": "starcharge_oam",
        "snapshot_id": snapshot_id,
        "scope_key": scope_key,
        "sync_mode": "full",
        "company_id": "company-nio",
        "org_code": "org-nio",
        "entity_type": "inventory",
        "snapshot_at": snapshot_at,
        "sequence": 1,
        "total_sequences": 1,
        "records": [record],
    }
    final_records = [{key: value for key, value in record.items() if key != "operation"}]
    manifest_payload = {
        "source_system": "starcharge_oam",
        "snapshot_id": snapshot_id,
        "scope_key": scope_key,
        "sync_mode": "full",
        "company_id": "company-nio",
        "org_code": "org-nio",
        "snapshot_at": snapshot_at,
        "entities": [
            {
                "entity_type": "inventory",
                "final_record_count": 1,
                "final_sha256": records_sha256(final_records),
                "delta_record_count": 1,
                "delta_sha256": records_sha256([record]),
                "batch_count": 1,
            }
        ],
    }

    with TestClient(app) as client:
        batch_body = canonical_bytes(batch_payload)
        batch_id = f"{snapshot_id}-inventory-1"
        accepted = client.post(
            "/api/integrations/oam/edge/snapshots/batches",
            content=batch_body,
            headers=edge_headers(batch_body, source, batch_id, int(time.time())),
        )
        assert accepted.status_code == 200, accepted.text

        bad_manifest = json.loads(json.dumps(manifest_payload))
        bad_manifest["entities"][0]["final_sha256"] = "0" * 64
        bad_body = canonical_bytes(bad_manifest)
        rejected = client.post(
            "/api/integrations/oam/edge/snapshots/complete",
            content=bad_body,
            headers=edge_headers(
                bad_body,
                source,
                f"{snapshot_id}-complete-bad",
                int(time.time()),
            ),
        )
        assert rejected.status_code == 409, rejected.text

        with SessionLocal() as db:
            current = db.scalar(
                select(ExternalSyncCurrentRecord).where(
                    ExternalSyncCurrentRecord.source_instance == source
                )
            )
            snapshot = db.scalar(
                select(ExternalSyncSnapshot).where(
                    ExternalSyncSnapshot.source_instance == source
                )
            )
            assert current is None
            assert snapshot is not None
            assert snapshot.status == "receiving"

        manifest_body = canonical_bytes(manifest_payload)
        completion_headers = edge_headers(
            manifest_body,
            source,
            f"{snapshot_id}-complete",
            int(time.time()),
        )
        completed = client.post(
            "/api/integrations/oam/edge/snapshots/complete",
            content=manifest_body,
            headers=completion_headers,
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "complete"
        assert completed.json()["entities"]["inventory"]["records"] == 1

        duplicate = client.post(
            "/api/integrations/oam/edge/snapshots/complete",
            content=manifest_body,
            headers=completion_headers,
        )
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["duplicate"] is True

        with SessionLocal() as db:
            current = db.scalar(
                select(ExternalSyncCurrentRecord).where(
                    ExternalSyncCurrentRecord.source_instance == source
                )
            )
            assert current is not None
            assert json.loads(current.payload_json)["qtyStock"] == 2


def test_oam_personnel_mapping_enables_sms_login_and_revokes_on_disable():
    engine.dispose()
    DB_PATH.unlink(missing_ok=True)
    source = f"pytest-personnel-{uuid.uuid4().hex[:12]}"
    snapshot_id = f"snapshot-{uuid.uuid4().hex}"
    snapshot_at = "2026-08-30T00:00:00+00:00"
    employee = {
        "business_key": "employee:oam-account-001",
        "source_updated_at": None,
        "data": {
            "employeeId": "employee-001",
            "accountId": "oam-account-001",
            "account": "engineer.001",
            "jobNo": "NIO001",
            "name": "映射测试工程师",
            "mobile": "13800000021",
            "status": 1,
            "isDelete": 0,
            "companyId": "company-nio",
            "orgCode": "org-nio",
            "loginEligible": True,
        },
        "operation": "upsert",
    }
    batch_payload = {
        "source_system": "starcharge_oam",
        "snapshot_id": snapshot_id,
        "scope_key": "all",
        "sync_mode": "full",
        "company_id": "company-star",
        "org_code": "org-star",
        "entity_type": "employee",
        "snapshot_at": snapshot_at,
        "sequence": 1,
        "total_sequences": 1,
        "records": [employee],
    }
    final_employee = [
        {key: value for key, value in employee.items() if key != "operation"}
    ]
    manifest_payload = {
        "source_system": "starcharge_oam",
        "snapshot_id": snapshot_id,
        "scope_key": "all",
        "sync_mode": "full",
        "company_id": "company-star",
        "org_code": "org-star",
        "snapshot_at": snapshot_at,
        "entities": [
            {
                "entity_type": "employee",
                "final_record_count": 1,
                "final_sha256": records_sha256(final_employee),
                "delta_record_count": 1,
                "delta_sha256": records_sha256([employee]),
                "batch_count": 1,
            }
        ],
    }

    with TestClient(app) as client:
        batch_body = canonical_bytes(batch_payload)
        accepted = client.post(
            "/api/integrations/oam/edge/snapshots/batches",
            content=batch_body,
            headers=edge_headers(
                batch_body,
                source,
                f"{snapshot_id}-employee-1",
                int(time.time()),
            ),
        )
        assert accepted.status_code == 200, accepted.text
        manifest_body = canonical_bytes(manifest_payload)
        completed = client.post(
            "/api/integrations/oam/edge/snapshots/complete",
            content=manifest_body,
            headers=edge_headers(
                manifest_body,
                source,
                f"{snapshot_id}-complete",
                int(time.time()),
            ),
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["personnel"]["eligible"] == 1

        with SessionLocal() as db:
            binding = db.scalar(select(OamPersonnelBinding))
            assert binding is not None
            assert binding.login_enabled is False
            assert db.scalar(select(User).where(User.mobile == "13800000021")) is None
            binding_id = binding.id

        admin_login = client.post(
            "/api/auth/login",
            json={
                "mobile": "18660255681",
                "password": "Temporary-Admin-Password-2026!",
            },
        )
        assert admin_login.status_code == 200, admin_login.text
        directory = client.get("/api/integrations/oam/personnel")
        assert directory.status_code == 200, directory.text
        assert directory.json()["summary"]["loginEligible"] == 1
        enabled = client.post(
            f"/api/integrations/oam/personnel/{binding_id}/enable",
            json={"role": "technician", "province": "浙江省"},
        )
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["loginEnabled"] is True

        with SessionLocal() as db:
            snapshot = db.scalar(
                select(ExternalSyncSnapshot).where(
                    ExternalSyncSnapshot.source_instance == source
                )
            )
            assert snapshot is not None
            for business_key, entity_type, data in (
                (
                    "material-apply:OWN-001",
                    "material_application",
                    {
                        "materialApplyId": "OWN-001",
                        "type": 4,
                        "status": 1,
                        "transferStatus": "waitingReceive",
                        "principalId": "oam-account-001",
                        "principalName": "映射测试工程师",
                        "createTime": "2026-08-30 08:00:00",
                    },
                ),
                (
                    "material-apply:OTHER-001",
                    "material_application",
                    {
                        "materialApplyId": "OTHER-001",
                        "type": 4,
                        "status": 1,
                        "transferStatus": "waitingReceive",
                        "principalId": "another-oam-account",
                        "principalName": "其他工程师",
                        "createTime": "2026-08-30 08:01:00",
                    },
                ),
                (
                    "material-apply-line:OWN-001:line-1",
                    "material_application_line",
                    {
                        "id": "line-1",
                        "materialApplyId": "OWN-001",
                        "materialCode": "ADQCPN0090",
                        "applyNum": 1,
                        "applicationStatus": "waitingReceive",
                    },
                ),
            ):
                db.add(
                    ExternalSyncCurrentRecord(
                        source_system="starcharge_oam",
                        source_instance=source,
                        scope_key="all",
                        entity_type=entity_type,
                        business_key=business_key,
                        payload_json=canonical_bytes(data).decode("utf-8"),
                        payload_sha256=hashlib.sha256(canonical_bytes(data)).hexdigest(),
                        last_snapshot_id=snapshot.id,
                    )
                )
            db.commit()

        requested = client.post(
            "/api/auth/sms/request", json={"mobile": "13800000021"}
        )
        assert requested.status_code == 200, requested.text
        mini_login = client.post(
            "/api/auth/miniprogram/sms-login",
            json={
                "mobile": "13800000021",
                "code": "246810",
                "device_id": "mapped-device-001",
                "device_name": "映射测试手机",
            },
        )
        assert mini_login.status_code == 200, mini_login.text
        mapped_access_token = mini_login.json()["access_token"]
        own_orders = client.get(
            "/api/integrations/oam/orders",
            headers={"Authorization": f"Bearer {mapped_access_token}"},
        )
        assert own_orders.status_code == 200, own_orders.text
        assert own_orders.json()["total"] == 1
        assert own_orders.json()["summary"]["activeLines"] == 1
        assert own_orders.json()["items"][0]["materialApplyId"] == "OWN-001"
        assert own_orders.json()["items"][0]["lines"][0]["materialCode"] == "ADQCPN0090"

        client.cookies.clear()
        admin_login = client.post(
            "/api/auth/login",
            json={
                "mobile": "18660255681",
                "password": "Temporary-Admin-Password-2026!",
            },
        )
        assert admin_login.status_code == 200, admin_login.text
        disabled = client.post(
            f"/api/integrations/oam/personnel/{binding_id}/disable"
        )
        assert disabled.status_code == 200, disabled.text
        assert client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {mapped_access_token}"},
        ).status_code == 401


def test_work_order_mirror_list_detail_and_person_scope():
    engine.dispose()
    DB_PATH.unlink(missing_ok=True)
    source = f"pytest-work-orders-{uuid.uuid4().hex[:12]}"
    with TestClient(app) as client:
        with SessionLocal() as db:
            snapshot = ExternalSyncSnapshot(
                source_system="starcharge_oam",
                source_instance=source,
                snapshot_id=f"snapshot-{uuid.uuid4().hex}",
                scope_key="work-orders:recent-30d",
                sync_mode="full",
                company_id="company-star",
                org_code="org-star",
                snapshot_at=datetime.fromisoformat("2026-08-30T00:00:00+00:00"),
                status="complete",
                manifest_json=json.dumps(
                    {
                        "entities": [
                            {"entity_type": "work_order"},
                            {"entity_type": "work_order_detail"},
                            {"entity_type": "work_order_relation"},
                        ]
                    }
                ),
            )
            db.add(snapshot)
            db.flush()
            work_orders = [
                {
                    "code": "WT-OWN-001",
                    "serviceCode": "SR-OWN-001",
                    "type": "故障工单",
                    "status": "处理中",
                    "statusCode": "processing",
                    "title": "主板故障",
                    "executor": "蔚来汽车销售服务有限公司-映射工程师",
                    "executorId": "oam-engineer-001",
                    "executorPhone": "13800000031",
                    "province": "浙江省",
                    "brandName": "长城",
                    "createTime": "2026-08-29 10:00:00",
                    "updateTime": "2026-08-30 08:00:00",
                    "deviceCodes": ["10000001"],
                },
                {
                    "code": "WT-OTHER-001",
                    "serviceCode": "SR-OTHER-001",
                    "type": "巡检工单",
                    "status": "已完结",
                    "statusCode": "end",
                    "title": "例行巡检",
                    "executor": "蔚来汽车销售服务有限公司-其他工程师",
                    "executorId": "oam-engineer-002",
                    "executorPhone": "13800000032",
                    "province": "江苏省",
                    "brandName": "星星充电",
                    "createTime": "2026-08-28 10:00:00",
                    "updateTime": "2026-08-29 08:00:00",
                    "deviceCodes": ["10000002"],
                },
            ]
            detail = {
                "ok": True,
                "summary": {
                    "id": "1001",
                    "code": "WT-OWN-001",
                    "status": "处理中",
                    "statusCode": "processing",
                    "type": "故障工单",
                    "executor": "蔚来汽车销售服务有限公司-映射工程师",
                    "province": "浙江省",
                    "city": "杭州市",
                    "area": "余杭区",
                    "brandName": "长城",
                    "warranty": "保外",
                    "createTime": "2026-08-29 10:00:00",
                },
                "counts": {"checkItems": 2, "quotations": 1},
                "targets": [{"deviceCode": "10000001", "model": "DH-TEST"}],
                "materials": [],
                "workItems": [
                    {
                        "id": "group-1",
                        "workItem": "2",
                        "items": [
                            {
                                "id": "item-yes-no",
                                "title": "是否0公里",
                                "columnType": "1",
                                "result": "N",
                                "media": [],
                            },
                            {
                                "id": "item-multi",
                                "title": "故障现象",
                                "columnType": "4",
                                "result": ["主板死机"],
                                "media": [],
                            },
                        ],
                    }
                ],
                "quotations": [
                    {
                        "id": "quotation-1",
                        "code": "BJ-001",
                        "totalPrice": 598.83,
                        "statusCode": "1",
                        "status": "已支付",
                        "collectionStatusCode": "paymentCollection",
                        "collectionStatus": "已回款",
                    }
                ],
                "timeline": [
                    {
                        "category": "操作",
                        "time": "2026-08-30 08:00:00",
                        "title": "保存执行内容",
                    }
                ],
                "relatedOrders": [],
                "errors": [],
            }
            for entity_type, business_key, payload in (
                ("work_order", "work-order:WT-OWN-001", work_orders[0]),
                ("work_order", "work-order:WT-OTHER-001", work_orders[1]),
                (
                    "work_order_detail",
                    "work-order-detail:WT-OWN-001",
                    detail,
                ),
                (
                    "work_order_relation",
                    "work-order-relation:WT-OWN-001:0000",
                    {
                        "workOrderCode": "WT-OWN-001",
                        "section": "relatedOrders",
                        "chunkIndex": 0,
                        "chunkCount": 1,
                        "items": [
                            {"code": "WT-RELATED-001"},
                            {"code": "WT-RELATED-002"},
                        ],
                    },
                ),
            ):
                encoded = canonical_bytes(payload)
                db.add(
                    ExternalSyncCurrentRecord(
                        source_system="starcharge_oam",
                        source_instance=source,
                        scope_key=snapshot.scope_key,
                        entity_type=entity_type,
                        business_key=business_key,
                        payload_json=encoded.decode("utf-8"),
                        payload_sha256=hashlib.sha256(encoded).hexdigest(),
                        last_snapshot_id=snapshot.id,
                    )
                )
            technician = User(
                mobile="13800000031",
                name="映射工程师",
                password_hash=hash_password("Technician-Test-2026!"),
                role="technician",
                province="浙江省",
                is_active=True,
                require_password_change=False,
            )
            db.add(technician)
            db.flush()
            db.add(
                OamPersonnelBinding(
                    source_instance=source,
                    oam_account_id="oam-engineer-001",
                    oam_employee_id="employee-001",
                    account="engineer.001",
                    job_no="NIO001",
                    name="映射工程师",
                    mobile="13800000031",
                    source_present=True,
                    source_active=True,
                    login_eligible=True,
                    eligibility_reason="可由管理员开通",
                    login_enabled=True,
                    user_id=technician.id,
                    last_seen_snapshot_id=snapshot.id,
                )
            )
            db.commit()

        admin_login = client.post(
            "/api/auth/login",
            json={
                "mobile": "18660255681",
                "password": "Temporary-Admin-Password-2026!",
            },
        )
        assert admin_login.status_code == 200, admin_login.text
        listed = client.get("/api/work-orders")
        assert listed.status_code == 200, listed.text
        assert listed.json()["summary"] == {
            "available": 2,
            "active": 1,
            "withDetail": 1,
            "statuses": {"processing": 1, "end": 1},
            "provinces": {"浙江省": 1, "江苏省": 1},
            "types": {"故障工单": 1, "巡检工单": 1},
            "warranties": {"保外": 1},
        }
        assert listed.json()["items"][0]["detailAvailable"] is True
        detail_response = client.get("/api/work-orders/WT-OWN-001")
        assert detail_response.status_code == 200, detail_response.text
        body = detail_response.json()
        assert body["workItems"][0]["items"][0]["result"] == "N"
        assert body["workItems"][0]["items"][1]["result"] == ["主板死机"]
        assert body["quotations"][0]["status"] == "已支付"
        assert body["quotations"][0]["collectionStatus"] == "已回款"
        assert [item["code"] for item in body["relatedOrders"]] == [
            "WT-RELATED-001",
            "WT-RELATED-002",
        ]
        assert client.get("/api/work-orders/WT-OTHER-001").status_code == 409

        client.cookies.clear()
        technician_login = client.post(
            "/api/auth/login",
            json={
                "mobile": "13800000031",
                "password": "Technician-Test-2026!",
            },
        )
        assert technician_login.status_code == 200, technician_login.text
        scoped = client.get("/api/work-orders")
        assert scoped.status_code == 200, scoped.text
        assert scoped.json()["summary"]["available"] == 1
        assert scoped.json()["items"][0]["code"] == "WT-OWN-001"
        assert client.get("/api/work-orders/WT-OTHER-001").status_code == 403
