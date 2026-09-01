#!/usr/bin/env python3
"""Read OAM locally and push a minimal, signed snapshot to RSC cloud staging."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
import re
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib import error, parse, request


ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work"
if str(WORK) not in sys.path:
    sys.path.insert(0, str(WORK))
PORTAL = WORK / "inventory_query_portal"
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

from inventory_query_portal.oam_read_client import get_paged  # noqa: E402
from query_oam_work_orders import (  # noqa: E402
    detail_order,
    epoch_ms,
    get_paged_parallel as get_work_orders_paged,
    normalized_list_row,
)


DEFAULT_API_BASE = ""
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "rsc-edge-sync"
MAX_SYNC_RECORD_BYTES = 64 * 1024
WORK_ORDER_RELATION_CHUNK_BYTES = 48 * 1024
SOURCE_PATTERN = re.compile(r"[^A-Za-z0-9._:-]+")
WAREHOUSE_FIELDS = (
    "companyId",
    "companyName",
    "orgCode",
    "name",
    "code",
    "status",
    "warehouseType",
    "warehouseAttribute",
    "positionType",
    "positionAttribute",
    "location",
    "plant",
)
INVENTORY_FIELDS = (
    "id",
    "stockId",
    "materialStockId",
    "companyId",
    "companyName",
    "orgCode",
    "warehouseName",
    "warehouseCode",
    "warehouseType",
    "warehouseAttribute",
    "positionName",
    "positionCode",
    "bizAttrCode",
    "bizTypeCode",
    "materialName",
    "materialCode",
    "materialModel",
    "materialStatus",
    "materialStockType",
    "qtyStock",
    "qtyLock",
    "unitName",
    "warehousePlant",
    "materialStockTypeRefNumber",
    "snNo",
)
APPLICATION_FIELDS = (
    "materialApplyId",
    "type",
    "status",
    "shipStatus",
    "transferStatus",
    "applicantId",
    "applicantName",
    "applicantType",
    "principalId",
    "principalName",
    "sourceCompanyId",
    "sourceCompanyName",
    "targetCompanyId",
    "targetCompanyName",
    "warehouseLocationCode",
    "warehouseLocationName",
    "warehousePositionCode",
    "warehousePositionName",
    "positionProperty",
    "info",
    "createAccountId",
    "createName",
    "createTime",
    "createTimeStamp",
    "updateAccountId",
    "updateName",
    "updateTime",
    "updateTimeStamp",
    "overTimeFlag",
)
APPLICATION_LINE_FIELDS = (
    "id",
    "materialApplyId",
    "materialId",
    "materialCode",
    "materialName",
    "materialDescription",
    "materialModel",
    "productCategory",
    "applyNum",
    "reduceNum",
    "rejectNum",
    "deliveryNum",
    "receivedNum",
    "routeNum",
    "unitCode",
    "unitName",
    "remark",
    "saveType",
    "sourceWarehouseCode",
    "sourceWarehouseName",
    "sourcePositionCode",
    "sourcePositionName",
    "sourcePositionProperty",
    "sourcePositionType",
    "targetWarehouseCode",
    "targetWarehouseName",
    "targetPositionCode",
    "targetPositionName",
    "targetPositionProperty",
    "targetPositionType",
    "isSnEnable",
)
TERMINAL_APPLICATION_STATUSES = {"finished", "invalided", "refused"}
TERMINAL_WORK_ORDER_STATUSES = {
    "end",
    "stopped",
    "closed",
    "rejected",
    "client_accept_pass",
    "platform_accept_pass",
    "source_accept_pass",
}
MOBILE_PATTERN = re.compile(r"^1[3-9]\d{9}$")
SECRET_PLACEHOLDER_MARKERS = (
    "replace-with",
    "replace_me",
    "replace-me",
    "change-me",
    "changeme",
)


class EdgeSyncError(RuntimeError):
    pass


def configured_sync_secret(value: str) -> bool:
    normalized = value.strip()
    lowered = normalized.lower()
    return len(normalized) >= 32 and not any(
        marker in lowered for marker in SECRET_PLACEHOLDER_MARKERS
    )


def validated_api_base(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        raise EdgeSyncError("必须显式配置RSC_EDGE_API_BASE后才能上传")
    parsed = parse.urlparse(normalized)
    loopback_http = parsed.scheme == "http" and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
    if parsed.scheme != "https" and not loopback_http:
        raise EdgeSyncError("RSC_EDGE_API_BASE必须使用HTTPS或本机回环隧道")
    if not parsed.netloc or parsed.username or parsed.password:
        raise EdgeSyncError("RSC_EDGE_API_BASE格式无效且不得包含凭据")
    return normalized


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def signing_message(
    timestamp: str,
    source_instance: str,
    batch_id: str,
    body: bytes,
) -> bytes:
    return b"\n".join(
        (
            timestamp.encode("ascii"),
            source_instance.encode("utf-8"),
            batch_id.encode("utf-8"),
            body,
        )
    )


def sign_request(
    secret: str,
    timestamp: str,
    source_instance: str,
    batch_id: str,
    body: bytes,
) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        signing_message(timestamp, source_instance, batch_id, body),
        hashlib.sha256,
    ).hexdigest()


def normalize_source_id(value: str) -> str:
    normalized = SOURCE_PATTERN.sub("-", value.strip()).strip("-")
    if not normalized:
        raise EdgeSyncError("本地同步器标识不能为空")
    return normalized[:128]


def selected_fields(row: dict[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for field in fields:
        value = row.get(field)
        if value is None or isinstance(value, (str, int, float, bool)):
            selected[field] = value
    return selected


def warehouse_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code:
            raise EdgeSyncError("OAM仓库存在缺少仓库编码的记录")
        if code in seen_codes:
            raise EdgeSyncError(f"OAM仓库编码重复：{code}")
        seen_codes.add(code)
        records.append(
            {
                "business_key": code,
                "source_updated_at": None,
                "data": selected_fields(row, WAREHOUSE_FIELDS),
            }
        )
    return sorted(records, key=lambda record: record["business_key"])


def inventory_business_key(row: dict[str, Any]) -> str:
    source_row_id = next(
        (
            str(row.get(key)).strip()
            for key in ("id", "stockId", "materialStockId")
            if row.get(key) not in (None, "")
        ),
        "",
    )
    if source_row_id:
        return f"stock:{source_row_id}"[:200]
    identity = [
        row.get("warehouseCode"),
        row.get("positionCode"),
        row.get("materialCode"),
        row.get("materialStockType"),
        row.get("materialStatus"),
        row.get("materialStockTypeRefNumber"),
        row.get("snNo"),
    ]
    digest = hashlib.sha256(canonical_json(identity)).hexdigest()
    return f"stock:{digest}"


def inventory_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records_by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        material_code = str(row.get("materialCode") or "").strip()
        warehouse_code = str(row.get("warehouseCode") or "").strip()
        if not material_code or not warehouse_code:
            raise EdgeSyncError("OAM库存存在缺少仓库编码或物料编码的记录")
        key = inventory_business_key(row)
        record = {
            "business_key": key,
            "source_updated_at": None,
            "data": selected_fields(row, INVENTORY_FIELDS),
        }
        previous = records_by_key.get(key)
        if previous is not None:
            raise EdgeSyncError(f"OAM库存业务键冲突：{key}")
        records_by_key[key] = record
    return sorted(records_by_key.values(), key=lambda record: record["business_key"])


def normalize_mobile(value: Any) -> str:
    mobile = re.sub(r"[\s-]+", "", str(value or "").strip())
    if mobile.startswith("+86"):
        mobile = mobile[3:]
    elif mobile.startswith("86") and len(mobile) == 13:
        mobile = mobile[2:]
    return mobile[:20]


def employee_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    account_ids: set[str] = set()
    eligible_mobiles: dict[str, str] = {}
    for row in rows:
        account_id = str(row.get("accountId") or "").strip()
        if not account_id:
            raise EdgeSyncError("OAM人员存在缺少账号标识的记录")
        if account_id in account_ids:
            raise EdgeSyncError(f"OAM人员账号标识重复：{account_id}")
        account_ids.add(account_id)
        company = row.get("company") if isinstance(row.get("company"), dict) else {}
        mobile = normalize_mobile(row.get("phoneNumber"))
        source_active = str(row.get("status") or "").strip() == "1" and str(
            row.get("isDelete") or "0"
        ).lower() in {"0", "false"}
        login_eligible = source_active and bool(MOBILE_PATTERN.fullmatch(mobile))
        if login_eligible:
            previous = eligible_mobiles.get(mobile)
            if previous and previous != account_id:
                raise EdgeSyncError(f"OAM在职人员手机号重复：{mobile}")
            eligible_mobiles[mobile] = account_id
        if not source_active:
            reason = "OAM账号已停用或删除"
        elif not MOBILE_PATTERN.fullmatch(mobile):
            reason = "OAM手机号缺失或格式不正确"
        else:
            reason = "可由管理员开通"
        city_codes = row.get("cityCodes")
        if not isinstance(city_codes, list) or not all(
            isinstance(item, (str, int, float, bool)) for item in city_codes
        ):
            city_codes = []
        data = {
            "employeeId": str(row.get("id") or "").strip(),
            "accountId": account_id,
            "account": str(row.get("account") or "").strip(),
            "jobNo": str(row.get("jobNo") or "").strip(),
            "name": str(row.get("name") or "").strip(),
            "mobile": mobile,
            "phonePrefix": str(row.get("phonePrefix") or "").strip(),
            "status": row.get("status"),
            "isDelete": row.get("isDelete"),
            "employeeType": row.get("type"),
            "larkId": str(row.get("larkId") or "").strip(),
            "cityCodes": city_codes,
            "companyInternalId": str(company.get("id") or "").strip(),
            "companyId": str(company.get("companyId") or "").strip(),
            "companyName": str(
                company.get("name") or company.get("companyName") or ""
            ).strip(),
            "companyCode": str(company.get("code") or "").strip(),
            "orgCode": str(company.get("orgCode") or "").strip(),
            "loginEligible": login_eligible,
            "loginEligibilityReason": reason,
            "updateTime": row.get("updateTime"),
        }
        records.append(
            {
                "business_key": f"employee:{account_id}",
                "source_updated_at": None,
                "data": data,
            }
        )
    return sorted(records, key=lambda record: record["business_key"])


def material_application_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        apply_id = str(row.get("materialApplyId") or "").strip()
        if not apply_id:
            raise EdgeSyncError("OAM调拨申请存在缺少单号的记录")
        if apply_id in seen:
            raise EdgeSyncError(f"OAM调拨申请单号重复：{apply_id}")
        seen.add(apply_id)
        records.append(
            {
                "business_key": f"material-apply:{apply_id}",
                "source_updated_at": None,
                "data": selected_fields(row, APPLICATION_FIELDS),
            }
        )
    return sorted(records, key=lambda record: record["business_key"])


def material_application_line_records(
    applications: list[dict[str, Any]],
    lines_by_application: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    applications_by_id = {
        str(row.get("materialApplyId") or "").strip(): row for row in applications
    }
    for apply_id, rows in lines_by_application.items():
        application = applications_by_id.get(apply_id)
        if application is None:
            raise EdgeSyncError(f"OAM调拨明细找不到主单：{apply_id}")
        for row in rows:
            returned_apply_id = str(row.get("materialApplyId") or apply_id).strip()
            if returned_apply_id != apply_id:
                raise EdgeSyncError(f"OAM调拨明细与主单绑定不一致：{apply_id}")
            detail_id = str(row.get("id") or "").strip()
            if not detail_id:
                identity = [
                    apply_id,
                    row.get("materialCode"),
                    row.get("sourcePositionCode"),
                    row.get("targetPositionCode"),
                ]
                detail_id = hashlib.sha256(canonical_json(identity)).hexdigest()
            business_key = f"material-apply-line:{apply_id}:{detail_id}"[:200]
            if business_key in seen:
                raise EdgeSyncError(f"OAM调拨明细业务键重复：{business_key}")
            seen.add(business_key)
            data = selected_fields(row, APPLICATION_LINE_FIELDS)
            data["materialApplyId"] = apply_id
            data["applicationStatus"] = application.get("transferStatus")
            data["applicationType"] = application.get("type")
            records.append(
                {
                    "business_key": business_key,
                    "source_updated_at": None,
                    "data": data,
                }
            )
    return sorted(records, key=lambda record: record["business_key"])


def work_order_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    seen: set[str] = set()
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code:
            raise EdgeSyncError("OAM工单存在缺少工单编号的记录")
        if code in seen:
            raise EdgeSyncError(f"OAM工单编号重复：{code}")
        seen.add(code)
        records.append(
            {
                "business_key": f"work-order:{code}",
                "source_updated_at": None,
                "data": row,
            }
        )
    return sorted(records, key=lambda record: record["business_key"])


def work_order_detail_records(
    details_by_code: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    relation_records: list[dict[str, Any]] = []
    for code, detail in sorted(details_by_code.items()):
        if str((detail.get("summary") or {}).get("code") or "").strip() != code:
            raise EdgeSyncError(f"OAM工单详情与列表编号不一致：{code}")
        stored_detail = detail
        if len(canonical_json(detail)) > MAX_SYNC_RECORD_BYTES:
            related_orders = detail.get("relatedOrders")
            if not isinstance(related_orders, list) or not related_orders:
                raise EdgeSyncError(
                    f"OAM工单基础详情超过单记录64KB上限：{code}"
                )
            stored_detail = {**detail, "relatedOrders": []}
            if len(canonical_json(stored_detail)) > MAX_SYNC_RECORD_BYTES:
                raise EdgeSyncError(
                    f"OAM工单移除关联工单后仍超过64KB：{code}"
                )

            chunks: list[list[Any]] = []
            current: list[Any] = []
            for item in related_orders:
                candidate = [*current, item]
                probe = {
                    "workOrderCode": code,
                    "section": "relatedOrders",
                    "chunkIndex": 9999,
                    "chunkCount": 9999,
                    "items": candidate,
                }
                if (
                    current
                    and len(canonical_json(probe)) > WORK_ORDER_RELATION_CHUNK_BYTES
                ):
                    chunks.append(current)
                    current = [item]
                else:
                    current = candidate
                single_probe = {**probe, "items": current}
                if len(canonical_json(single_probe)) > MAX_SYNC_RECORD_BYTES:
                    raise EdgeSyncError(
                        f"OAM工单单条关联记录超过64KB：{code}"
                    )
            if current:
                chunks.append(current)

            for index, items in enumerate(chunks):
                payload = {
                    "workOrderCode": code,
                    "section": "relatedOrders",
                    "chunkIndex": index,
                    "chunkCount": len(chunks),
                    "items": items,
                }
                if len(canonical_json(payload)) > MAX_SYNC_RECORD_BYTES:
                    raise EdgeSyncError(
                        f"OAM工单关联分块超过64KB：{code}/{index}"
                    )
                relation_records.append(
                    {
                        "business_key": (
                            f"work-order-relation:{code}:{index:04d}"
                        ),
                        "source_updated_at": None,
                        "data": payload,
                    }
                )
        records.append(
            {
                "business_key": f"work-order-detail:{code}",
                "source_updated_at": None,
                "data": stored_detail,
            }
        )
    return records, relation_records


def load_work_orders(
    *,
    days: int,
    target_company_id: str,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    total, source_rows, complete = get_work_orders_paged(
        "/work_order/list",
        {
            "createTimeStartTimeStamp": epoch_ms(start),
            "createTimeEndTimeStamp": epoch_ms(end),
        },
        referer="/tickets/tickets/list",
        page_size=100,
        max_pages=100,
    )
    if not complete or total != len(source_rows):
        raise EdgeSyncError(
            f"OAM工单列表未完整读取：应有{total}条，实际{len(source_rows)}条"
        )
    scoped_rows = [
        row
        for row in source_rows
        if str(row.get("authCompanyId") or "").strip() == target_company_id
    ]
    if not scoped_rows:
        raise EdgeSyncError("配置的蔚来企业范围内没有可读取工单")
    normalized = [normalized_list_row(row) for row in scoped_rows]
    source_by_code = {
        str(row.get("workOrderCode") or "").strip(): row for row in scoped_rows
    }
    if len(source_by_code) != len(scoped_rows):
        raise EdgeSyncError("OAM工单列表存在空编号或重复编号")
    return normalized, source_by_code


def refresh_work_order_details(
    *,
    rows: list[dict[str, Any]],
    source_by_code: dict[str, dict[str, Any]],
    cache: dict[str, Any],
    detail_limit: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], dict[str, int]]:
    cached_records = cache.get("records")
    if not isinstance(cached_records, dict):
        cached_records = {}
    current_codes = {str(row.get("code") or "").strip() for row in rows}
    current_codes.discard("")
    retained = {
        code: value
        for code, value in cached_records.items()
        if code in current_codes and isinstance(value, dict)
    }
    row_by_code = {str(row.get("code") or "").strip(): row for row in rows}
    changed = []
    for code, row in row_by_code.items():
        cached = retained.get(code) or {}
        if cached.get("sourceUpdateTime") != row.get("updateTime"):
            changed.append(row)
    changed.sort(
        key=lambda row: (
            str(row.get("statusCode") or "") not in TERMINAL_WORK_ORDER_STATUSES,
            str(row.get("updateTime") or ""),
            str(row.get("createTime") or ""),
        ),
        reverse=True,
    )
    selected = changed[:detail_limit]
    refreshed: dict[str, dict[str, Any]] = {}

    def normalize_optional_sections(detail: dict[str, Any]) -> dict[str, Any]:
        summary = detail.get("summary")
        status_code = (
            str(summary.get("statusCode") or "").strip()
            if isinstance(summary, dict)
            else ""
        )
        warnings = [
            item for item in detail.get("warnings", []) if isinstance(item, dict)
        ]
        errors = []
        downgraded = False
        for item in detail.get("errors", []):
            if not isinstance(item, dict):
                errors.append(item)
                continue
            message = str(item.get("message") or "")
            is_unavailable_pre_receive_process = (
                status_code == "wait_receive"
                and item.get("section") == "流程节点"
                and item.get("endpoint") == "/work_order/process_node_data"
                and "E00001" in message
            )
            if is_unavailable_pre_receive_process:
                downgraded = True
                warnings.append(
                    {
                        **item,
                        "reason": "待接单阶段尚无流程节点",
                    }
                )
            else:
                errors.append(item)
        if not downgraded:
            return detail
        counts = detail.get("counts")
        normalized_counts = (
            {**counts, "errors": len(errors), "warnings": len(warnings)}
            if isinstance(counts, dict)
            else counts
        )
        return {
            **detail,
            "counts": normalized_counts,
            "errors": errors,
            "warnings": warnings,
        }

    def fetch(row: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        code = str(row.get("code") or "").strip()
        source = source_by_code.get(code) or {}
        identifier = str(source.get("id") or source.get("workOrderId") or "").strip()
        if not identifier:
            raise EdgeSyncError(f"OAM工单缺少详情ID：{code}")
        detail = normalize_optional_sections(detail_order(identifier, code))
        if detail.get("errors"):
            sections = ",".join(
                str(item.get("section") or "未知")
                for item in detail["errors"]
                if isinstance(item, dict)
            )
            raise EdgeSyncError(f"OAM工单详情读取不完整：{code}（{sections}）")
        return code, detail

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(selected)))) as pool:
        futures = {pool.submit(fetch, row): row for row in selected}
        for future in as_completed(futures):
            code, detail = future.result()
            refreshed[code] = detail

    for row in selected:
        code = str(row.get("code") or "").strip()
        retained[code] = {
            "sourceUpdateTime": row.get("updateTime"),
            "detail": refreshed[code],
        }
    next_cache = {
        "version": 1,
        "cursor": 0,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "records": retained,
    }
    details = {
        code: value["detail"]
        for code, value in retained.items()
        if isinstance(value.get("detail"), dict)
    }
    return details, next_cache, {
        "listed": len(rows),
        "cachedDetails": len(details),
        "changedDetails": len(changed),
        "refreshedDetails": len(refreshed),
    }


def run_oam_health(*, scheduled: bool) -> dict[str, Any]:
    command = [
        sys.executable,
        str(WORK / "global_business_session_health.py"),
        "--require",
        "oam",
    ]
    if scheduled:
        command.append("--consume-notification")
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
    )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise EdgeSyncError("OAM会话健康检查未返回有效结果") from exc
    if completed.returncode != 0 or not result.get("ok"):
        oam = (result.get("components") or {}).get("oam") or {}
        status_value = oam.get("status") or "unknown"
        reason = oam.get("reasonCode") or "health_check_failed"
        detail = oam.get("detail") or "OAM实时读取检查失败"
        raise EdgeSyncError(f"OAM会话检查未通过：{status_value}/{reason}，{detail}")
    return result


def load_warehouses(
    warehouse_code: str | None,
    expected_company_id: str,
    expected_org_code: str,
) -> list[dict[str, Any]]:
    total, rows = get_paged("/warehouse/list", {})
    if len(rows) != total:
        raise EdgeSyncError(
            f"OAM仓库分页读取不完整：sourceTotal={total}, fetched={len(rows)}"
        )
    if warehouse_code:
        candidates = [
            row for row in rows if str(row.get("code") or "").strip() == warehouse_code
        ]
        if not candidates:
            raise EdgeSyncError(f"未找到仓库编码：{warehouse_code}")
        if len(candidates) != 1:
            raise EdgeSyncError(f"仓库编码不唯一：{warehouse_code}")
    else:
        candidates = rows

    scoped = [
        row
        for row in candidates
        if str(row.get("companyId") or "").strip() == expected_company_id
        and str(row.get("orgCode") or "").strip() == expected_org_code
    ]
    if warehouse_code and not scoped:
        raise EdgeSyncError(f"仓库不属于配置的企业范围：{warehouse_code}")
    if not scoped:
        raise EdgeSyncError("配置的企业范围内没有可读取仓库")
    return scoped


def bind_inventory_scope(
    row: dict[str, Any],
    warehouse: dict[str, Any],
) -> dict[str, Any]:
    copied = dict(row)
    for field in ("companyId", "orgCode", "warehouseCode"):
        warehouse_field = field if field != "warehouseCode" else "code"
        expected = str(warehouse.get(warehouse_field) or "").strip()
        actual = str(copied.get(field) or "").strip()
        if actual and actual != expected:
            raise EdgeSyncError(
                f"库存记录与仓库绑定不一致：{field}={actual or '<empty>'}"
            )
        copied[field] = expected
    copied.setdefault("companyName", warehouse.get("companyName"))
    copied.setdefault("warehouseName", warehouse.get("name"))
    copied.setdefault("warehouseType", warehouse.get("warehouseType"))
    copied.setdefault("warehouseAttribute", warehouse.get("warehouseAttribute"))
    return copied


def load_inventory(warehouses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    for warehouse in warehouses:
        payload = {
            "warehouseCode": warehouse.get("code"),
            "warehouseAttribute": warehouse.get("warehouseAttribute"),
            "warehouseType": warehouse.get("warehouseType"),
            "querySource": "PC",
            "snDisplayFlag": 1
            if warehouse.get("warehouseType") == "supplyWarehouse"
            else 0,
        }
        total, rows = get_paged("/material_stock/list", payload)
        if len(rows) != total:
            raise EdgeSyncError(
                "OAM库存分页读取不完整："
                f"warehouse={warehouse.get('code')}, "
                f"sourceTotal={total}, fetched={len(rows)}"
            )
        for row in rows:
            all_rows.append(bind_inventory_scope(row, warehouse))
    return all_rows


def load_employees(
    expected_company_id: str,
    expected_org_code: str,
    expected_internal_id: str,
) -> list[dict[str, Any]]:
    total, rows = get_paged("/employee/query", {}, referer="/system/employee")
    if len(rows) != total:
        raise EdgeSyncError(
            f"OAM人员分页读取不完整：sourceTotal={total}, fetched={len(rows)}"
        )
    scoped: list[dict[str, Any]] = []
    for row in rows:
        company = row.get("company")
        if not isinstance(company, dict):
            continue
        internal_id = str(company.get("id") or "").strip()
        company_id = str(company.get("companyId") or "").strip()
        org_code = str(company.get("orgCode") or "").strip()
        id_matches = internal_id == expected_internal_id
        company_matches = company_id == expected_company_id
        if id_matches != company_matches:
            raise EdgeSyncError("OAM人员企业内部ID与企业ID范围不一致")
        if not id_matches:
            continue
        if org_code and org_code != expected_org_code:
            raise EdgeSyncError("OAM人员组织编码与配置范围不一致")
        copied = dict(row)
        copied["company"] = {
            **company,
            "orgCode": expected_org_code,
        }
        scoped.append(copied)
    if not scoped:
        raise EdgeSyncError("配置的企业范围内没有可读取人员")
    return scoped


def load_material_applications(
    source_company_id: str,
    target_company_id: str,
) -> list[dict[str, Any]]:
    total, rows = get_paged(
        "/material/apply/list",
        {"types": "1,2,4"},
        referer="/warehouse/materialsAllot/list",
    )
    if len(rows) != total:
        raise EdgeSyncError(
            f"OAM调拨分页读取不完整：sourceTotal={total}, fetched={len(rows)}"
        )
    scoped = [
        row
        for row in rows
        if str(row.get("sourceCompanyId") or "").strip() == source_company_id
        and str(row.get("targetCompanyId") or "").strip() == target_company_id
    ]
    if not scoped:
        raise EdgeSyncError("配置的星星到蔚来企业范围内没有调拨申请")
    return scoped


def load_active_application_lines(
    applications: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    active = [
        row
        for row in applications
        if str(row.get("transferStatus") or "").strip()
        not in TERMINAL_APPLICATION_STATUSES
    ]
    result: dict[str, list[dict[str, Any]]] = {}
    for application in active:
        apply_id = str(application.get("materialApplyId") or "").strip()
        total, rows = get_paged(
            "/material/apply/detail/list",
            {"materialApplyId": apply_id},
            referer="/warehouse/materialsAllot/detail",
        )
        if len(rows) != total:
            raise EdgeSyncError(
                "OAM调拨明细分页读取不完整："
                f"applyId={apply_id}, sourceTotal={total}, fetched={len(rows)}"
            )
        result[apply_id] = rows
    return result


def validated_api_url(api_base: str, endpoint: str) -> str:
    base = validated_api_base(api_base)
    return f"{base}/{endpoint.lstrip('/')}"


def send_signed_json(
    *,
    api_base: str,
    endpoint: str,
    secret: str,
    source_instance: str,
    batch_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    body = canonical_json(payload)
    timestamp = str(int(time.time()))
    signature = sign_request(
        secret,
        timestamp,
        source_instance,
        batch_id,
        body,
    )
    req = request.Request(
        validated_api_url(api_base, endpoint),
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-RSC-Edge-Source": source_instance,
            "X-RSC-Edge-Timestamp": timestamp,
            "X-RSC-Edge-Batch": batch_id,
            "X-RSC-Edge-Signature": signature,
        },
    )
    opener = request.build_opener(request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=60) as response:
            response_body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8", errors="replace")
        raise EdgeSyncError(f"云端拒绝同步：HTTP {exc.code} {response_body[:500]}") from exc
    except error.URLError as exc:
        raise EdgeSyncError(f"无法连接云端同步接口：{exc.reason}") from exc
    try:
        result = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise EdgeSyncError("云端同步接口返回格式无效") from exc
    if not result.get("ok"):
        raise EdgeSyncError("云端同步接口未确认接收")
    return result


def chunks(records: list[dict[str, Any]], size: int):
    for start in range(0, len(records), size):
        yield records[start : start + size]


def record_sha256(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(record)).hexdigest()


def records_sha256(records: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_json(records)).hexdigest()


def load_json_file(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EdgeSyncError(f"本地同步状态文件无效：{path}") from exc
    if not isinstance(value, dict):
        raise EdgeSyncError(f"本地同步状态格式无效：{path}")
    return value


def write_private_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(
                value,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def local_sync_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise EdgeSyncError("已有OAM边缘同步任务正在运行") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def scope_key_for(warehouse_code: str | None) -> str:
    return f"warehouse:{warehouse_code}" if warehouse_code else "all"


def work_order_scope_key(days: int) -> str:
    return f"work-orders:recent-{days}d"


def prepare_entity(
    *,
    entity_type: str,
    records: list[dict[str, Any]],
    previous_index: dict[str, str],
    sync_mode: str,
) -> dict[str, Any]:
    records = sorted(records, key=lambda record: record["business_key"])
    current_index = {
        record["business_key"]: record_sha256(record) for record in records
    }
    records_by_key = {record["business_key"]: record for record in records}
    delta_records: list[dict[str, Any]] = []
    for business_key in sorted(current_index):
        if sync_mode == "full" or previous_index.get(business_key) != current_index[
            business_key
        ]:
            delta_records.append({**records_by_key[business_key], "operation": "upsert"})
    if sync_mode == "incremental":
        for business_key in sorted(set(previous_index) - set(current_index)):
            delta_records.append(
                {
                    "business_key": business_key,
                    "source_updated_at": None,
                    "data": {},
                    "operation": "delete",
                }
            )
    delta_records.sort(key=lambda record: record["business_key"])
    return {
        "entityType": entity_type,
        "finalRecordCount": len(records),
        "finalSha256": records_sha256(records),
        "deltaRecordCount": len(delta_records),
        "deltaSha256": records_sha256(delta_records),
        "deltaRecords": delta_records,
        "finalIndex": current_index,
    }


def build_outbox(
    *,
    source_instance: str,
    scope_key: str,
    warehouse_filter: str | None,
    company_id: str,
    org_code: str,
    snapshot_at: str,
    snapshots: dict[str, list[dict[str, Any]]],
    state: dict[str, Any],
    force_full: bool,
    batch_size: int,
) -> dict[str, Any]:
    scope_state = (state.get("scopes") or {}).get(scope_key) or {}
    previous_entities = scope_state.get("entities") or {}
    sync_mode = "full" if force_full or not scope_state else "incremental"
    snapshot_id = (
        f"s-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid.uuid4().hex[:16]}"
    )
    entities: dict[str, Any] = {}
    for entity_type, records in snapshots.items():
        previous_index = (previous_entities.get(entity_type) or {}).get("index") or {}
        entity = prepare_entity(
            entity_type=entity_type,
            records=records,
            previous_index=previous_index,
            sync_mode=sync_mode,
        )
        entity["batchCount"] = (
            len(entity["deltaRecords"]) + batch_size - 1
        ) // batch_size
        entities[entity_type] = entity
    return {
        "version": 2,
        "sourceInstance": source_instance,
        "snapshotId": snapshot_id,
        "scopeKey": scope_key,
        "syncMode": sync_mode,
        "companyId": company_id,
        "orgCode": org_code,
        "warehouseFilter": warehouse_filter,
        "snapshotAt": snapshot_at,
        "batchSize": batch_size,
        "entities": entities,
    }


def persist_completed_state(
    state_file: Path,
    state: dict[str, Any],
    outbox: dict[str, Any],
) -> None:
    scopes = state.setdefault("scopes", {})
    scopes[outbox["scopeKey"]] = {
        "snapshotId": outbox["snapshotId"],
        "snapshotAt": outbox["snapshotAt"],
        "companyId": outbox["companyId"],
        "orgCode": outbox["orgCode"],
        "entities": {
            entity_type: {
                "records": entity["finalRecordCount"],
                "sha256": entity["finalSha256"],
                "index": entity["finalIndex"],
            }
            for entity_type, entity in outbox["entities"].items()
        },
    }
    state["version"] = 2
    state["sourceInstance"] = outbox["sourceInstance"]
    state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    write_private_json(state_file, state)


def upload_outbox(
    *,
    outbox: dict[str, Any],
    api_base: str,
    secret: str,
    state_file: Path,
    state: dict[str, Any],
) -> dict[str, Any]:
    source_instance = outbox["sourceInstance"]
    snapshot_id = outbox["snapshotId"]
    batch_size = int(outbox["batchSize"])
    accepted_batches = 0
    accepted_records = 0
    for entity_type, entity in outbox["entities"].items():
        batch_count = int(entity["batchCount"])
        for sequence, batch_records in enumerate(
            chunks(entity["deltaRecords"], batch_size),
            start=1,
        ):
            payload = {
                "source_system": "starcharge_oam",
                "snapshot_id": snapshot_id,
                "scope_key": outbox["scopeKey"],
                "sync_mode": outbox["syncMode"],
                "company_id": outbox["companyId"],
                "org_code": outbox["orgCode"],
                "entity_type": entity_type,
                "snapshot_at": outbox["snapshotAt"],
                "sequence": sequence,
                "total_sequences": batch_count,
                "records": batch_records,
            }
            response = send_signed_json(
                api_base=api_base,
                endpoint="integrations/oam/edge/snapshots/batches",
                secret=secret,
                source_instance=source_instance,
                batch_id=f"{snapshot_id}-{entity_type}-{sequence}",
                payload=payload,
            )
            accepted_batches += 1
            accepted_records += int(response.get("accepted_records") or 0)

    manifest = {
        "source_system": "starcharge_oam",
        "snapshot_id": snapshot_id,
        "scope_key": outbox["scopeKey"],
        "sync_mode": outbox["syncMode"],
        "company_id": outbox["companyId"],
        "org_code": outbox["orgCode"],
        "snapshot_at": outbox["snapshotAt"],
        "entities": [
            {
                "entity_type": entity_type,
                "final_record_count": entity["finalRecordCount"],
                "final_sha256": entity["finalSha256"],
                "delta_record_count": entity["deltaRecordCount"],
                "delta_sha256": entity["deltaSha256"],
                "batch_count": entity["batchCount"],
            }
            for entity_type, entity in sorted(outbox["entities"].items())
        ],
    }
    completion = send_signed_json(
        api_base=api_base,
        endpoint="integrations/oam/edge/snapshots/complete",
        secret=secret,
        source_instance=source_instance,
        batch_id=f"{snapshot_id}-complete",
        payload=manifest,
    )
    persist_completed_state(state_file, state, outbox)
    return {
        "snapshotId": snapshot_id,
        "syncMode": outbox["syncMode"],
        "acceptedBatches": accepted_batches,
        "acceptedRecords": accepted_records,
        "completion": completion,
    }


def resume_pending_outboxes(
    *,
    outbox_dir: Path,
    source_instance: str,
    company_id: str,
    org_code: str,
    api_base: str,
    secret: str,
    state_file: Path,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compatibility entrypoint that now fails closed.

    A payload captured before an interruption may no longer describe current
    OAM state. V1.0 therefore requires a fresh read instead of replaying it.
    """

    _ = (
        outbox_dir,
        source_instance,
        company_id,
        org_code,
        api_base,
        secret,
        state_file,
        state,
    )
    raise EdgeSyncError(
        "历史同步包禁止盲目重放；请保留失败证据并重新读取OAM生成新快照"
    )


def quarantine_pending_outboxes(outbox_dir: Path) -> list[str]:
    """Isolate interrupted payloads without uploading or deleting evidence."""

    outbox_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(outbox_dir, 0o700)
    quarantine_dir = outbox_dir / "quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(quarantine_dir, 0o700)
    quarantined: list[str] = []
    for path in sorted(outbox_dir.glob("*.json")):
        target = quarantine_dir / path.name
        if target.exists():
            raise EdgeSyncError(f"隔离目录已存在同名同步包：{path.name}")
        os.replace(path, target)
        quarantined.append(path.name)
    return quarantined


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="星星OAM到RSC云端的本地只读同步器")
    parser.add_argument(
        "--entity",
        choices=(
            "warehouses",
            "inventory",
            "employees",
            "orders",
            "work-orders",
            "all",
        ),
        default="all",
        help="要同步的数据范围",
    )
    parser.add_argument("--warehouse-code", help="仅同步一个仓库编码")
    parser.add_argument("--dry-run", action="store_true", help="只查询并输出统计，不上传")
    parser.add_argument(
        "--scheduled",
        action="store_true",
        help="计划任务模式；认证失败时领取一次统一登录提醒",
    )
    parser.add_argument(
        "--api-base",
        default=os.getenv("RSC_EDGE_API_BASE", DEFAULT_API_BASE),
    )
    parser.add_argument(
        "--source-id",
        default=os.getenv("RSC_EDGE_SOURCE_ID", socket.gethostname()),
    )
    parser.add_argument(
        "--company-id",
        default=os.getenv("RSC_EDGE_EXPECTED_COMPANY_ID", ""),
        help="只同步这个OAM企业ID；必须明确配置",
    )
    parser.add_argument(
        "--org-code",
        default=os.getenv("RSC_EDGE_EXPECTED_ORG_CODE", ""),
        help="只同步这个OAM组织编码；必须明确配置",
    )
    parser.add_argument(
        "--target-company-id",
        default=os.getenv("RSC_EDGE_TARGET_COMPANY_ID", ""),
        help="人员及调拨目标所属的蔚来OAM企业ID",
    )
    parser.add_argument(
        "--target-org-code",
        default=os.getenv("RSC_EDGE_TARGET_ORG_CODE", ""),
        help="人员所属的蔚来OAM组织编码",
    )
    parser.add_argument(
        "--employee-company-internal-id",
        default=os.getenv("RSC_EDGE_EMPLOYEE_COMPANY_INTERNAL_ID", ""),
        help="人员所属的蔚来OAM企业内部ID",
    )
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--work-order-days",
        type=int,
        default=30,
        help="工单镜像覆盖的最近天数",
    )
    parser.add_argument(
        "--work-order-detail-limit",
        type=int,
        default=12,
        help="本次最多刷新多少张已变化工单详情",
    )
    parser.add_argument(
        "--work-order-cache-file",
        type=Path,
        default=Path(
            os.getenv(
                "RSC_EDGE_WORK_ORDER_CACHE_FILE",
                DEFAULT_CONFIG_DIR / "work_order_details.json",
            )
        ).expanduser(),
    )
    parser.add_argument(
        "--force-full",
        action="store_true",
        help="忽略本地增量索引，发送一个完整快照",
    )
    parser.add_argument(
        "--resume-only",
        action="store_true",
        help="已停用：历史同步包不得盲目重放，必须重新读取OAM",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(
            os.getenv(
                "RSC_EDGE_STATE_FILE",
                DEFAULT_CONFIG_DIR / "state.json",
            )
        ).expanduser(),
    )
    parser.add_argument(
        "--outbox-dir",
        type=Path,
        default=Path(
            os.getenv(
                "RSC_EDGE_OUTBOX_DIR",
                DEFAULT_CONFIG_DIR / "outbox",
            )
        ).expanduser(),
    )
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path(
            os.getenv(
                "RSC_EDGE_LOCK_FILE",
                DEFAULT_CONFIG_DIR / "sync.lock",
            )
        ).expanduser(),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.batch_size <= 500:
        raise EdgeSyncError("批次大小必须在1到500之间")
    if not 1 <= args.work_order_days <= 365:
        raise EdgeSyncError("工单同步天数必须在1到365之间")
    if not 1 <= args.work_order_detail_limit <= 200:
        raise EdgeSyncError("单次工单详情刷新数必须在1到200之间")
    source_instance = normalize_source_id(args.source_id)
    expected_company_id = args.company_id.strip()
    expected_org_code = args.org_code.strip()
    if not expected_company_id or not expected_org_code:
        raise EdgeSyncError("必须配置OAM企业ID和组织编码后才能读取")
    include_national = args.warehouse_code is None
    needs_people = args.entity == "employees" or (
        args.entity == "all" and include_national
    )
    needs_material_orders = args.entity == "orders" or (
        args.entity == "all" and include_national
    )
    needs_work_orders = args.entity == "work-orders"
    if args.warehouse_code and args.entity in {"employees", "orders", "work-orders"}:
        raise EdgeSyncError("人员、调拨申请和工单是全国范围，不能同时指定单个仓库")
    target_company_id = args.target_company_id.strip()
    target_org_code = args.target_org_code.strip()
    employee_company_internal_id = args.employee_company_internal_id.strip()
    if (needs_people or needs_material_orders or needs_work_orders) and not target_company_id:
        raise EdgeSyncError("必须配置蔚来OAM企业ID后才能同步人员、调拨申请或工单")
    if needs_people and (
        not target_org_code or not employee_company_internal_id
    ):
        raise EdgeSyncError("必须配置蔚来OAM组织编码和企业内部ID后才能同步人员")
    secret = os.getenv("RSC_EDGE_SYNC_SECRET", "")
    api_base = args.api_base.strip()
    if not args.dry_run:
        if not configured_sync_secret(secret):
            raise EdgeSyncError("RSC_EDGE_SYNC_SECRET未配置、仍为占位值或长度不足32位")
        api_base = validated_api_base(api_base)
    with local_sync_lock(args.lock_file):
        state = load_json_file(
            args.state_file,
            {"version": 2, "sourceInstance": source_instance, "scopes": {}},
        )
        quarantined: list[str] = []
        if not args.dry_run:
            quarantined = quarantine_pending_outboxes(args.outbox_dir)
        if args.resume_only:
            raise EdgeSyncError(
                "--resume-only已停用；失败同步包已隔离，请执行正常同步重新查询OAM"
            )

        run_oam_health(scheduled=args.scheduled)
        snapshots: dict[str, list[dict[str, Any]]] = {}
        next_work_order_cache: dict[str, Any] | None = None
        work_order_summary: dict[str, int] | None = None
        needs_warehouses = args.entity in {"warehouses", "inventory", "all"}
        warehouses: list[dict[str, Any]] = []
        if needs_warehouses:
            warehouses = load_warehouses(
                args.warehouse_code,
                expected_company_id,
                expected_org_code,
            )
        if args.entity in {"warehouses", "all"}:
            snapshots["warehouse"] = warehouse_records(warehouses)
        if args.entity in {"inventory", "all"}:
            snapshots["inventory"] = inventory_records(load_inventory(warehouses))
        if needs_people:
            snapshots["employee"] = employee_records(
                load_employees(
                    target_company_id,
                    target_org_code,
                    employee_company_internal_id,
                )
            )
        if needs_material_orders:
            applications = load_material_applications(
                expected_company_id,
                target_company_id,
            )
            snapshots["material_application"] = material_application_records(
                applications
            )
            snapshots["material_application_line"] = (
                material_application_line_records(
                    applications,
                    load_active_application_lines(applications),
                )
            )
        if needs_work_orders:
            work_orders, source_by_code = load_work_orders(
                days=args.work_order_days,
                target_company_id=target_company_id,
            )
            work_order_cache = load_json_file(
                args.work_order_cache_file,
                {"version": 1, "cursor": 0, "records": {}},
            )
            if work_order_cache.get("version") != 1:
                raise EdgeSyncError("本地工单详情缓存版本无效")
            details, next_work_order_cache, work_order_summary = (
                refresh_work_order_details(
                    rows=work_orders,
                    source_by_code=source_by_code,
                    cache=work_order_cache,
                    detail_limit=args.work_order_detail_limit,
                )
            )
            snapshots["work_order"] = work_order_records(work_orders)
            detail_records, relation_records = work_order_detail_records(details)
            snapshots["work_order_detail"] = detail_records
            snapshots["work_order_relation"] = relation_records

        snapshot_at = datetime.now(timezone.utc).isoformat()
        scope_key = (
            work_order_scope_key(args.work_order_days)
            if needs_work_orders
            else scope_key_for(args.warehouse_code)
        )
        outbox = build_outbox(
            source_instance=source_instance,
            scope_key=scope_key,
            warehouse_filter=args.warehouse_code,
            company_id=expected_company_id,
            org_code=expected_org_code,
            snapshot_at=snapshot_at,
            snapshots=snapshots,
            state=state,
            force_full=args.force_full,
            batch_size=args.batch_size,
        )
        summary: dict[str, Any] = {
            "ok": True,
            "mode": "dry_run" if args.dry_run else "staging_only",
            "source": source_instance,
            "snapshotId": outbox["snapshotId"],
            "scopeKey": outbox["scopeKey"],
            "syncMode": outbox["syncMode"],
            "companyScope": {
                "companyId": expected_company_id,
                "orgCode": expected_org_code,
            },
            "targetCompanyScope": None
            if not (needs_people or needs_material_orders or needs_work_orders)
            else {
                "companyId": target_company_id,
                "orgCode": target_org_code if needs_people else None,
                "employeeCompanyInternalId": employee_company_internal_id
                if needs_people
                else None,
            },
            "warehouseFilter": args.warehouse_code,
            "snapshotAt": snapshot_at,
            "quarantinedOutboxes": quarantined,
            "entities": {
                entity_type: {
                    "records": entity["finalRecordCount"],
                    "sha256": entity["finalSha256"],
                    "deltaRecords": entity["deltaRecordCount"],
                    "deltaSha256": entity["deltaSha256"],
                    "batches": entity["batchCount"],
                }
                for entity_type, entity in outbox["entities"].items()
            },
            "workOrders": work_order_summary,
        }
        if not args.dry_run:
            outbox_path = args.outbox_dir / f"{outbox['snapshotId']}.json"
            write_private_json(outbox_path, outbox)
            summary["upload"] = upload_outbox(
                outbox=outbox,
                api_base=api_base,
                secret=secret,
                state_file=args.state_file,
                state=state,
            )
            if next_work_order_cache is not None:
                write_private_json(args.work_order_cache_file, next_work_order_cache)
            outbox_path.unlink()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EdgeSyncError as error_message:
        print(
            json.dumps(
                {"ok": False, "error": str(error_message)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from None
