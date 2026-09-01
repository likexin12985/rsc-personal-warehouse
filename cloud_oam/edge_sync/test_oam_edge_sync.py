import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cloud_oam.edge_sync import oam_edge_sync


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
        normalized, source_by_code = oam_edge_sync.load_work_orders(
            days=30,
            target_company_id=COMPANY_ID,
        )

    assert [row["code"] for row in normalized] == ["WT-NIO-001"]
    assert list(source_by_code) == ["WT-NIO-001"]

    with patch.object(
        oam_edge_sync,
        "get_work_orders_paged",
        return_value=(3, rows, False),
    ):
        with pytest.raises(oam_edge_sync.EdgeSyncError, match="未完整读取"):
            oam_edge_sync.load_work_orders(days=30, target_company_id=COMPANY_ID)


def test_refresh_work_order_details_keeps_verified_cache_and_refreshes_changes():
    rows = [
        {
            "code": "WT-NIO-001",
            "statusCode": "processing",
            "updateTime": "2026-08-30 10:00:00",
            "createTime": "2026-08-29 10:00:00",
        },
        {
            "code": "WT-NIO-002",
            "statusCode": "end",
            "updateTime": "2026-08-29 11:00:00",
            "createTime": "2026-08-28 10:00:00",
        },
    ]
    cached_detail = {
        "summary": {"code": "WT-NIO-002"},
        "errors": [],
    }
    cache = {
        "version": 1,
        "cursor": 0,
        "records": {
            "WT-NIO-002": {
                "sourceUpdateTime": "2026-08-29 11:00:00",
                "detail": cached_detail,
            }
        },
    }
    source_by_code = {
        "WT-NIO-001": {"id": "1001", "workOrderCode": "WT-NIO-001"},
        "WT-NIO-002": {"id": "1002", "workOrderCode": "WT-NIO-002"},
    }
    live_detail = {"summary": {"code": "WT-NIO-001"}, "errors": []}
    with patch.object(oam_edge_sync, "detail_order", return_value=live_detail) as read:
        details, next_cache, summary = oam_edge_sync.refresh_work_order_details(
            rows=rows,
            source_by_code=source_by_code,
            cache=cache,
            detail_limit=1,
        )

    read.assert_called_once_with("1001", "WT-NIO-001")
    assert details == {
        "WT-NIO-001": live_detail,
        "WT-NIO-002": cached_detail,
    }
    assert next_cache["records"]["WT-NIO-001"]["sourceUpdateTime"] == (
        "2026-08-30 10:00:00"
    )
    assert summary == {
        "listed": 2,
        "cachedDetails": 2,
        "changedDetails": 1,
        "refreshedDetails": 1,
    }


def test_refresh_work_order_details_allows_only_wait_receive_process_warning():
    rows = [
        {
            "code": "WT-NIO-001",
            "statusCode": "wait_receive",
            "updateTime": "2026-08-30 10:00:00",
            "createTime": "2026-08-30 09:00:00",
        }
    ]
    source_by_code = {
        "WT-NIO-001": {"id": "1001", "workOrderCode": "WT-NIO-001"}
    }
    source_error = {
        "section": "流程节点",
        "endpoint": "/work_order/process_node_data",
        "message": "E00001 未知错误",
    }
    detail = {
        "summary": {"code": "WT-NIO-001", "statusCode": "wait_receive"},
        "counts": {"errors": 1},
        "errors": [source_error],
    }
    with patch.object(oam_edge_sync, "detail_order", return_value=detail):
        details, _, _ = oam_edge_sync.refresh_work_order_details(
            rows=rows,
            source_by_code=source_by_code,
            cache={"version": 1, "cursor": 0, "records": {}},
            detail_limit=1,
        )

    normalized = details["WT-NIO-001"]
    assert normalized["errors"] == []
    assert normalized["counts"] == {"errors": 0, "warnings": 1}
    assert normalized["warnings"][0]["reason"] == "待接单阶段尚无流程节点"

    blocking = {
        **detail,
        "summary": {"code": "WT-NIO-001", "statusCode": "processing"},
    }
    with patch.object(oam_edge_sync, "detail_order", return_value=blocking):
        with pytest.raises(oam_edge_sync.EdgeSyncError, match="流程节点"):
            oam_edge_sync.refresh_work_order_details(
                rows=rows,
                source_by_code=source_by_code,
                cache={"version": 1, "cursor": 0, "records": {}},
                detail_limit=1,
            )


def test_work_order_detail_records_rejects_code_mismatch_and_oversize():
    with pytest.raises(oam_edge_sync.EdgeSyncError, match="编号不一致"):
        oam_edge_sync.work_order_detail_records(
            {"WT-NIO-001": {"summary": {"code": "WT-NIO-002"}}}
        )
    with pytest.raises(oam_edge_sync.EdgeSyncError, match="64KB"):
        oam_edge_sync.work_order_detail_records(
            {
                "WT-NIO-001": {
                    "summary": {"code": "WT-NIO-001"},
                    "payload": "x" * (65 * 1024),
                }
            }
        )


def test_work_order_detail_records_split_large_related_orders_losslessly():
    related_orders = [
        {"code": f"WT-RELATED-{index:04d}", "description": "x" * 900}
        for index in range(100)
    ]
    details, relation_chunks = oam_edge_sync.work_order_detail_records(
        {
            "WT-NIO-001": {
                "summary": {"code": "WT-NIO-001"},
                "relatedOrders": related_orders,
            }
        }
    )

    assert details[0]["data"]["relatedOrders"] == []
    assert len(relation_chunks) > 1
    assert all(
        len(oam_edge_sync.canonical_json(record["data"])) <= 64 * 1024
        for record in relation_chunks
    )
    assert [
        item
        for record in relation_chunks
        for item in record["data"]["items"]
    ] == related_orders
