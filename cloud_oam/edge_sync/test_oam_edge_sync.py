import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The production read clients intentionally remain outside the hosted source
# repository because they consume the local shared OAM session.  Keep this
# contract suite runnable in isolated CI by installing non-networking import
# stubs only when those local files are absent (or when explicitly requested by
# this test process).  Every test patches the transport before use.
LOCAL_PORTAL = ROOT / "work" / "inventory_query_portal"
USE_OFFLINE_IMPORT_STUBS = (
    os.getenv("RSC_EDGE_TEST_FORCE_IMPORT_STUBS") == "1"
    or not (LOCAL_PORTAL / "oam_read_client.py").is_file()
    or not (LOCAL_PORTAL / "query_oam_work_orders.py").is_file()
)
STUB_MODULE_NAMES = (
    "inventory_query_portal",
    "inventory_query_portal.oam_read_client",
    "query_oam_work_orders",
)
previous_import_modules = {
    name: sys.modules.get(name) for name in STUB_MODULE_NAMES
}
if USE_OFFLINE_IMPORT_STUBS:
    portal_package = ModuleType("inventory_query_portal")
    portal_package.__path__ = []  # type: ignore[attr-defined]
    read_client = ModuleType("inventory_query_portal.oam_read_client")

    def _offline_get_paged(*_args, **_kwargs):
        raise AssertionError("isolated tests must patch the OAM read transport")

    read_client.get_paged = _offline_get_paged  # type: ignore[attr-defined]
    portal_package.oam_read_client = read_client  # type: ignore[attr-defined]
    sys.modules["inventory_query_portal"] = portal_package
    sys.modules["inventory_query_portal.oam_read_client"] = read_client

    work_order_client = ModuleType("query_oam_work_orders")

    def _offline_work_order_page(*_args, **_kwargs):
        raise AssertionError("isolated tests must patch the OAM work-order transport")

    def _epoch_ms(value: datetime) -> int:
        return int(value.timestamp() * 1000)

    def _normalized_list_row(row):
        area_parts = [part.strip() for part in str(row.get("areaName") or "").split("-")]
        return {
            "id": str(row.get("id") or row.get("workOrderId") or "").strip(),
            "code": str(row.get("workOrderCode") or "").strip(),
            "statusCode": str(row.get("workOrderStatus") or "").strip(),
            "executorId": str(
                row.get("stepExecutorId")
                or row.get("assigneeId")
                or row.get("transfereeId")
                or ""
            ).strip(),
            "authCompanyId": str(row.get("authCompanyId") or "").strip(),
            "province": area_parts[0] if area_parts and area_parts[0] else "",
            "updateTime": str(row.get("updateTime") or "").strip(),
        }

    work_order_client.epoch_ms = _epoch_ms  # type: ignore[attr-defined]
    work_order_client.get_paged_parallel = _offline_work_order_page  # type: ignore[attr-defined]
    work_order_client.normalized_list_row = _normalized_list_row  # type: ignore[attr-defined]
    sys.modules["query_oam_work_orders"] = work_order_client

try:
    from cloud_oam.edge_sync import oam_edge_sync
finally:
    if USE_OFFLINE_IMPORT_STUBS:
        for module_name, previous_module in previous_import_modules.items():
            if previous_module is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = previous_module


COMPANY_ID = "company-nio"
ORG_CODE = "org-nio"


def test_upload_configuration_has_no_live_default_and_fails_closed():
    assert oam_edge_sync.DEFAULT_API_BASE == ""
    assert oam_edge_sync.configured_sync_secret("s" * 32)
    assert not oam_edge_sync.configured_sync_secret(
        "replace-with-a-32-character-secret-value"
    )
    with pytest.raises(oam_edge_sync.EdgeSyncError, match="显式配置"):
        oam_edge_sync.validated_api_base("")
    with pytest.raises(oam_edge_sync.EdgeSyncError, match="HTTPS"):
        oam_edge_sync.validated_api_base("http://example.com/api")
    with pytest.raises(oam_edge_sync.EdgeSyncError, match="不得包含凭据"):
        oam_edge_sync.validated_api_base("https://user:secret@example.com/api")
    assert (
        oam_edge_sync.validated_api_base("http://127.0.0.1:18001/api/")
        == "http://127.0.0.1:18001/api"
    )


def warehouse(**overrides):
    row = {
        "code": "10605611",
        "name": "江苏省服务商库_新",
        "companyId": COMPANY_ID,
        "companyName": "蔚来汽车销售服务有限公司",
        "orgCode": ORG_CODE,
        "warehouseType": "serviceProviderWarehouse",
        "warehouseAttribute": "goodPosition",
    }
    row.update(overrides)
    return row


def test_load_warehouses_filters_exact_company_scope():
    rows = [warehouse(), warehouse(code="other", companyId="another-company")]
    with patch.object(oam_edge_sync, "get_paged", return_value=(2, rows)):
        result = oam_edge_sync.load_warehouses(None, COMPANY_ID, ORG_CODE)

    assert [item["code"] for item in result] == ["10605611"]


def test_load_warehouses_rejects_mismatched_requested_warehouse():
    rows = [warehouse(companyId="another-company")]
    with patch.object(oam_edge_sync, "get_paged", return_value=(1, rows)):
        with pytest.raises(oam_edge_sync.EdgeSyncError, match="企业范围"):
            oam_edge_sync.load_warehouses("10605611", COMPANY_ID, ORG_CODE)


def test_load_warehouses_rejects_incomplete_pagination():
    with patch.object(oam_edge_sync, "get_paged", return_value=(2, [warehouse()])):
        with pytest.raises(oam_edge_sync.EdgeSyncError, match="分页读取不完整"):
            oam_edge_sync.load_warehouses(None, COMPANY_ID, ORG_CODE)


def test_record_builders_reject_missing_and_duplicate_business_keys():
    with pytest.raises(oam_edge_sync.EdgeSyncError, match="缺少仓库编码"):
        oam_edge_sync.warehouse_records([warehouse(code="")])
    with pytest.raises(oam_edge_sync.EdgeSyncError, match="仓库编码重复"):
        oam_edge_sync.warehouse_records([warehouse(), warehouse()])

    row = {
        "id": "stock-1",
        "warehouseCode": "10605611",
        "materialCode": "ADQCPN0090",
    }
    with pytest.raises(oam_edge_sync.EdgeSyncError, match="业务键冲突"):
        oam_edge_sync.inventory_records([row, dict(row)])
    with pytest.raises(oam_edge_sync.EdgeSyncError, match="缺少仓库编码或物料编码"):
        oam_edge_sync.inventory_records([{**row, "materialCode": ""}])


def test_bind_inventory_scope_adds_trusted_binding():
    result = oam_edge_sync.bind_inventory_scope(
        {"materialCode": "ADQCPN0090"},
        warehouse(),
    )

    assert result["companyId"] == COMPANY_ID
    assert result["orgCode"] == ORG_CODE
    assert result["warehouseCode"] == "10605611"


def test_bind_inventory_scope_rejects_conflicting_binding():
    with pytest.raises(oam_edge_sync.EdgeSyncError, match="绑定不一致"):
        oam_edge_sync.bind_inventory_scope(
            {
                "materialCode": "ADQCPN0090",
                "warehouseCode": "unexpected",
            },
            warehouse(),
        )


def stock_record(key: str, quantity: int):
    return {
        "business_key": key,
        "source_updated_at": None,
        "data": {
            "warehouseCode": "10605611",
            "materialCode": "ADQCPN0090",
            "qtyStock": quantity,
        },
    }


def test_prepare_entity_builds_incremental_updates_and_deletes():
    old_a = stock_record("stock:a", 1)
    old_b = stock_record("stock:b", 2)
    previous_index = {
        old_a["business_key"]: oam_edge_sync.record_sha256(old_a),
        old_b["business_key"]: oam_edge_sync.record_sha256(old_b),
    }

    result = oam_edge_sync.prepare_entity(
        entity_type="inventory",
        records=[stock_record("stock:a", 3), stock_record("stock:c", 1)],
        previous_index=previous_index,
        sync_mode="incremental",
    )

    assert result["finalRecordCount"] == 2
    assert [record["business_key"] for record in result["deltaRecords"]] == [
        "stock:a",
        "stock:b",
        "stock:c",
    ]
    assert [record["operation"] for record in result["deltaRecords"]] == [
        "upsert",
        "delete",
        "upsert",
    ]


def test_prepare_entity_emits_empty_delta_when_unchanged():
    current = stock_record("stock:a", 1)
    result = oam_edge_sync.prepare_entity(
        entity_type="inventory",
        records=[current],
        previous_index={
            current["business_key"]: oam_edge_sync.record_sha256(current)
        },
        sync_mode="incremental",
    )

    assert result["finalRecordCount"] == 1
    assert result["deltaRecordCount"] == 0
    assert result["deltaSha256"] == oam_edge_sync.records_sha256([])


def test_interrupted_outbox_is_quarantined_and_never_replayed(tmp_path):
    outbox_dir = tmp_path / "outbox"
    outbox_path = outbox_dir / "snapshot-test.json"
    outbox = {
        "version": 2,
        "sourceInstance": "admin-mac",
        "companyId": COMPANY_ID,
        "orgCode": ORG_CODE,
    }
    oam_edge_sync.write_private_json(outbox_path, outbox)
    with patch.object(oam_edge_sync, "upload_outbox") as upload:
        quarantined = oam_edge_sync.quarantine_pending_outboxes(outbox_dir)
    upload.assert_not_called()
    assert quarantined == ["snapshot-test.json"]
    assert not outbox_path.exists()
    assert (outbox_dir / "quarantine" / "snapshot-test.json").exists()

    with pytest.raises(oam_edge_sync.EdgeSyncError, match="禁止盲目重放"):
        oam_edge_sync.resume_pending_outboxes(
            outbox_dir=outbox_dir,
            source_instance="admin-mac",
            company_id=COMPANY_ID,
            org_code=ORG_CODE,
            api_base="http://127.0.0.1:18001/api",
            secret="s" * 32,
            state_file=tmp_path / "state.json",
            state={"version": 2, "sourceInstance": "admin-mac", "scopes": {}},
        )


def test_employee_records_only_include_login_directory_whitelist():
    row = {
        "id": 11,
        "accountId": "account-11",
        "account": "engineer.11",
        "jobNo": "NIO0011",
        "name": "测试工程师",
        "phoneNumber": "+86 138-0000-0011",
        "phonePrefix": "+86",
        "status": 1,
        "isDelete": 0,
        "type": 2,
        "larkId": "lark-11",
        "cityCodes": ["330100"],
        "bankAccount": "must-not-leave-device",
        "company": {
            "id": 2572,
            "companyId": COMPANY_ID,
            "name": "蔚来汽车销售服务有限公司",
            "orgCode": ORG_CODE,
        },
    }

    result = oam_edge_sync.employee_records([row])

    assert result[0]["business_key"] == "employee:account-11"
    assert result[0]["data"]["mobile"] == "13800000011"
    assert result[0]["data"]["loginEligible"] is True
    assert "bankAccount" not in result[0]["data"]
    assert "company" not in result[0]["data"]


def test_employee_records_reject_duplicate_active_mobile():
    def row(account_id: str):
        return {
            "accountId": account_id,
            "name": account_id,
            "phoneNumber": "13800000011",
            "status": 1,
            "isDelete": 0,
            "company": {},
        }

    with pytest.raises(oam_edge_sync.EdgeSyncError, match="手机号重复"):
        oam_edge_sync.employee_records([row("account-a"), row("account-b")])


def test_load_employees_filters_nio_company_scope():
    rows = [
        {
            "accountId": "nio-account",
            "company": {
                "id": 2572,
                "companyId": COMPANY_ID,
                "orgCode": ORG_CODE,
            },
        },
        {
            "accountId": "other-account",
            "company": {"id": 3000, "companyId": "other", "orgCode": "other"},
        },
    ]
    with patch.object(oam_edge_sync, "get_paged", return_value=(2, rows)):
        result = oam_edge_sync.load_employees(COMPANY_ID, ORG_CODE, "2572")

    assert [row["accountId"] for row in result] == ["nio-account"]


def test_material_application_scope_and_active_lines():
    application = {
        "materialApplyId": "202608300001",
        "sourceCompanyId": "star-company",
        "targetCompanyId": COMPANY_ID,
        "status": "waitingReceive",
        "transferStatus": "waitingReceive",
        "type": 4,
    }
    responses = [
        (2, [application, {**application, "materialApplyId": "other", "targetCompanyId": "other"}]),
        (
            1,
            [
                {
                    "id": "line-1",
                    "materialApplyId": application["materialApplyId"],
                    "materialCode": "ADQCPN0090",
                    "applyNum": 2,
                }
            ],
        ),
    ]
    with patch.object(oam_edge_sync, "get_paged", side_effect=responses):
        applications = oam_edge_sync.load_material_applications(
            "star-company", COMPANY_ID
        )
        lines = oam_edge_sync.load_active_application_lines(applications)

    records = oam_edge_sync.material_application_line_records(applications, lines)
    assert [row["materialApplyId"] for row in applications] == ["202608300001"]
    assert records[0]["data"]["materialCode"] == "ADQCPN0090"
    assert records[0]["data"]["applicationStatus"] == "waitingReceive"


def test_load_work_orders_requires_complete_exact_company_scope():
    rows = [
        {
            "id": "1001",
            "workOrderCode": "WT-NIO-001",
            "workOrderStatus": "processing",
            "authCompanyId": COMPANY_ID,
            "areaName": "浙江省-杭州市-余杭区",
        },
        {
            "id": "1002",
            "workOrderCode": "WT-OTHER-001",
            "workOrderStatus": "end",
            "authCompanyId": "other-company",
            "areaName": "江苏省-南京市-建邺区",
        },
    ]
    with patch.object(
        oam_edge_sync,
        "get_work_orders_paged",
        return_value=(2, rows, True),
    ):
        normalized = oam_edge_sync.load_work_orders(
            days=30,
            target_company_id=COMPANY_ID,
        )

    assert [row["code"] for row in normalized] == ["WT-NIO-001"]

    with patch.object(
        oam_edge_sync,
        "get_work_orders_paged",
        return_value=(3, rows, False),
    ):
        with pytest.raises(oam_edge_sync.EdgeSyncError, match="未完整读取"):
            oam_edge_sync.load_work_orders(days=30, target_company_id=COMPANY_ID)


def test_work_order_records_emit_minimal_timezone_aware_projection():
    rows = [
        {
            "id": "1001",
            "code": "WT-NIO-001",
            "status": "处理中",
            "statusCode": "processing",
            "executor": "工程师姓名不得出边缘最小投影",
            "executorId": "employee-external-001",
            "executorPhone": "13800000000",
            "authCompanyId": COMPANY_ID,
            "province": "浙江省",
            "updateTime": "2026-08-30 10:00:00",
            "deviceCodes": ["sensitive-device"],
        }
    ]

    records = oam_edge_sync.work_order_records(
        rows,
        expected_company_id=COMPANY_ID,
    )

    assert records == [
        {
            "business_key": "work-order:WT-NIO-001",
            "source_updated_at": "2026-08-30T02:00:00+00:00",
            "data": {
                "id": "1001",
                "code": "WT-NIO-001",
                "statusCode": "processing",
                "executorId": "employee-external-001",
                "authCompanyId": COMPANY_ID,
                "province": "浙江省",
                "updateTime": "2026-08-30 10:00:00",
            },
        }
    ]
    assert "executorPhone" not in records[0]["data"]
    assert "executor" not in records[0]["data"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"id": ""}, "稳定来源ID"),
        ({"statusCode": "future_unknown_state"}, "状态未纳入正式映射"),
        ({"executorId": ""}, "缺少执行人来源ID"),
        ({"authCompanyId": "other-company"}, "企业范围不一致"),
        ({"updateTime": ""}, "缺少来源更新时间"),
        ({"updateTime": "not-a-time"}, "来源更新时间格式无效"),
        ({"updateTime": "2026-08-30"}, "来源更新时间格式无效"),
    ],
)
def test_work_order_records_fail_closed_on_incomplete_source_evidence(
    overrides,
    message,
):
    row = {
        "id": "1001",
        "code": "WT-NIO-001",
        "statusCode": "processing",
        "executorId": "employee-external-001",
        "authCompanyId": COMPANY_ID,
        "province": "浙江省",
        "updateTime": "2026-08-30T10:00:00+08:00",
        **overrides,
    }
    with pytest.raises(oam_edge_sync.EdgeSyncError, match=message):
        oam_edge_sync.work_order_records(
            [row],
            expected_company_id=COMPANY_ID,
        )


def work_order_snapshot():
    return {
        "work_order": oam_edge_sync.work_order_records(
            [
                {
                    "id": "1001",
                    "code": "WT-NIO-001",
                    "statusCode": "processing",
                    "executorId": "employee-external-001",
                    "authCompanyId": COMPANY_ID,
                    "province": "浙江省",
                    "updateTime": "2026-08-30 10:00:00",
                }
            ],
            expected_company_id=COMPANY_ID,
        )
    }


def build_work_order_outbox(*, state, force_full=False, **overrides):
    values = {
        "source_instance": "admin-mac",
        "scope_key": "work-orders:recent-30d",
        "warehouse_filter": None,
        "company_id": COMPANY_ID,
        "org_code": ORG_CODE,
        "snapshot_at": "2026-08-30T02:01:00+00:00",
        "snapshots": work_order_snapshot(),
        "state": state,
        "force_full": force_full,
        "batch_size": 100,
    }
    values.update(overrides)
    return oam_edge_sync.build_outbox(**values)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"source_instance": "second-mac"}, "来源实例"),
        ({"company_id": "other-company"}, "companyId"),
        ({"org_code": "other-org"}, "orgCode"),
    ],
)
def test_work_order_state_coordinate_drift_requires_explicit_full(
    override,
    message,
    tmp_path,
):
    state = {"version": 2, "sourceInstance": "admin-mac", "scopes": {}}
    completed = build_work_order_outbox(state=state, force_full=True)
    oam_edge_sync.persist_completed_state(tmp_path / "state.json", state, completed)

    with pytest.raises(oam_edge_sync.EdgeSyncError, match=message):
        build_work_order_outbox(state=state, **override)

    reset = build_work_order_outbox(state=state, force_full=True, **override)
    assert reset["syncMode"] == "full"
    assert reset["entities"]["work_order"]["deltaRecordCount"] == 1


def test_legacy_scope_with_entities_cannot_be_reused_without_coordinates():
    record = work_order_snapshot()["work_order"][0]
    state = {
        "version": 2,
        "sourceInstance": "admin-mac",
        "scopes": {
            "work-orders:recent-30d": {
                "companyId": COMPANY_ID,
                "orgCode": ORG_CODE,
                "entities": {
                    "work_order": {
                        "index": {
                            record["business_key"]: oam_edge_sync.record_sha256(
                                record
                            )
                        }
                    }
                },
            }
        },
    }

    with pytest.raises(oam_edge_sync.EdgeSyncError, match="scope.sourceInstance"):
        build_work_order_outbox(state=state)
    assert build_work_order_outbox(state=state, force_full=True)["syncMode"] == "full"


def test_malformed_previous_index_requires_explicit_full():
    state = {
        "version": 2,
        "sourceInstance": "admin-mac",
        "scopes": {
            "work-orders:recent-30d": {
                "sourceInstance": "admin-mac",
                "scopeKey": "work-orders:recent-30d",
                "companyId": COMPANY_ID,
                "orgCode": ORG_CODE,
                "entities": {"work_order": {"index": []}},
            }
        },
    }

    with pytest.raises(oam_edge_sync.EdgeSyncError, match="index格式无效"):
        build_work_order_outbox(state=state)
    assert build_work_order_outbox(state=state, force_full=True)["syncMode"] == "full"


def test_completed_state_binds_source_company_org_and_scope(tmp_path):
    state = {"version": 2, "sourceInstance": "admin-mac", "scopes": {}}
    full = build_work_order_outbox(state=state, force_full=True)
    state_file = tmp_path / "state.json"
    oam_edge_sync.persist_completed_state(state_file, state, full)

    scope_state = state["scopes"]["work-orders:recent-30d"]
    assert {
        key: scope_state[key]
        for key in ("sourceInstance", "scopeKey", "companyId", "orgCode")
    } == {
        "sourceInstance": "admin-mac",
        "scopeKey": "work-orders:recent-30d",
        "companyId": COMPANY_ID,
        "orgCode": ORG_CODE,
    }
    assert build_work_order_outbox(state=state)["syncMode"] == "incremental"


def test_formal_work_order_feed_rejects_detail_or_relation_entities():
    snapshots = {
        **work_order_snapshot(),
        "work_order_detail": [],
        "work_order_relation": [],
    }
    with pytest.raises(oam_edge_sync.EdgeSyncError, match="只能包含work_order"):
        build_work_order_outbox(
            state={"version": 2, "sourceInstance": "admin-mac", "scopes": {}},
            force_full=True,
            snapshots=snapshots,
        )


def test_formal_work_order_upload_manifest_contains_only_work_order(tmp_path):
    state = {"version": 2, "sourceInstance": "admin-mac", "scopes": {}}
    outbox = build_work_order_outbox(state=state, force_full=True)
    requests = []

    def accept(**kwargs):
        requests.append(kwargs)
        accepted_records = len(kwargs["payload"].get("records", []))
        return {"ok": True, "accepted_records": accepted_records}

    with patch.object(oam_edge_sync, "send_signed_json", side_effect=accept):
        oam_edge_sync.upload_outbox(
            outbox=outbox,
            api_base="http://127.0.0.1:18001/api",
            secret="s" * 32,
            state_file=tmp_path / "state.json",
            state=state,
        )

    batches = [item for item in requests if item["endpoint"].endswith("/batches")]
    completion = next(
        item for item in requests if item["endpoint"].endswith("/complete")
    )
    assert {item["payload"]["entity_type"] for item in batches} == {
        "work_order"
    }
    assert [entity["entity_type"] for entity in completion["payload"]["entities"]] == [
        "work_order"
    ]


def test_daily_scheduler_forces_one_complete_cycle_without_detail_refresh():
    scheduler = (Path(__file__).with_name("run_scheduled_sync.sh")).read_text(
        encoding="utf-8"
    )

    assert "LAST_FULL_DATE_FILE" in scheduler
    assert scheduler.count('"${force_full_args[@]}"') == 2
    assert "exit_code == 0" in scheduler
    assert "work-order-detail-limit" not in scheduler
    assert "work-order-cache" not in scheduler


def run_fake_scheduled_sync(tmp_path, *, work_order_exit="0"):
    config_dir = tmp_path / ".config" / "rsc-edge-sync"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "env").write_text("", encoding="utf-8")
    calls_file = tmp_path / "calls.txt"
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >>"${RSC_TEST_CALLS}"\n'
        'case " $* " in\n'
        '  *" --entity work-orders "*) exit "${RSC_TEST_WORK_ORDER_EXIT:-0}" ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o700)
    scheduler = Path(__file__).with_name("run_scheduled_sync.sh")
    environment = {
        "HOME": str(tmp_path),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "RSC_EDGE_PYTHON": str(fake_python),
        "RSC_TEST_CALLS": str(calls_file),
        "RSC_TEST_WORK_ORDER_EXIT": work_order_exit,
    }
    completed = subprocess.run(
        ["/bin/zsh", str(scheduler)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    calls = calls_file.read_text(encoding="utf-8").splitlines()
    return completed, calls, config_dir / "last-completed-full-sync-date"


def test_daily_scheduler_marks_full_only_after_both_scopes_complete(tmp_path):
    first, first_calls, marker = run_fake_scheduled_sync(tmp_path)

    assert first.returncode == 0
    assert len(first_calls) == 2
    assert all("--force-full" in call for call in first_calls)
    assert marker.is_file()

    second, all_calls, _ = run_fake_scheduled_sync(tmp_path)
    assert second.returncode == 0
    assert len(all_calls) == 4
    assert all("--force-full" not in call for call in all_calls[-2:])


def test_daily_scheduler_does_not_mark_partial_full_cycle(tmp_path):
    failed, first_calls, marker = run_fake_scheduled_sync(
        tmp_path,
        work_order_exit="7",
    )

    assert failed.returncode == 7
    assert len(first_calls) == 2
    assert all("--force-full" in call for call in first_calls)
    assert not marker.exists()

    retried, all_calls, marker = run_fake_scheduled_sync(tmp_path)
    assert retried.returncode == 0
    assert len(all_calls) == 4
    assert all("--force-full" in call for call in all_calls[-2:])
    assert marker.is_file()
