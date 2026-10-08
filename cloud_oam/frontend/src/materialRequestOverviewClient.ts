import { api } from "./api";
import { hasFormalPermission, hasFormalRole } from "./clientPolicy";
import type { AccessContext } from "./types";

export const OVERVIEW_DIMENSIONS: Record<string, { label: string; states: Record<string, string> }> = {
  request_status: { label: "申请", states: { draft: "草稿", submitted: "已提交", approval_in_progress: "审批中", returned: "已退回", partially_approved: "部分批准", approved: "已批准", rejected: "已驳回", withdrawn: "已撤回", cancellation_pending: "取消处理中", cancelled: "已取消" } },
  approval_level_1: { label: "区域一级审批", states: {} },
  approval_level_2: { label: "蔚来总部二级审批", states: {} },
  approval_level_3: { label: "星星总部三级审批", states: {} },
  allocation_status: { label: "货源分配", states: { not_allocated: "未分配", partially_allocated: "部分分配", allocated: "已分配", shortage: "缺货" } },
  reservation_status: { label: "库存占用", states: { not_reserved: "未占用", pending: "待占用", reserved: "已占用", partially_released: "部分释放", released: "已释放", fulfilled: "已履约" } },
  outbound_status: { label: "出库", states: { not_started: "未开始", pending_pick: "待拣货", picked: "已拣货", outbound: "已出库" } },
  shipment_status: { label: "发运", states: { not_started: "未开始", pending_handover: "待交运", shipped: "已发货", in_transit: "运输中", exception: "异常" } },
  logistics_signature_status: { label: "物流签收", states: { not_signed: "未签收", signed: "已签收", refused: "拒收", exception: "异常" } },
  oam_receipt_status: { label: "OAM 收货", states: { not_occurred: "未发生", synced: "已同步", exception: "异常" } },
  personal_inbound_status: { label: "个人仓入库", states: { pending_acceptance: "待验收", partially_accepted: "部分验收", accepted: "已验收", posted: "已入账", not_started: "未开始" } },
  notification_status: { label: "通知", states: { not_started: "未开始", queued: "排队中", sent: "已发送", delivered: "已送达", read: "已读", failed: "失败" } },
  reconciliation_status: { label: "同步与对账", states: { pending: "待同步", staged: "已暂存", validated: "已校验", reconciled: "已对账", conflict: "冲突", failed: "失败", not_started: "未开始" } },
};
for (const level of [1, 2, 3]) {
  OVERVIEW_DIMENSIONS[`approval_level_${level}`].states = {
    not_started: "未开始", pending: "待开启", open: "待审批", awaiting_external_evidence: "待外部审批证据",
    evidence_pending_verification: "待证据复核", approved: "已批准", partially_approved: "部分批准",
    rejected: "已驳回", returned: "已退回", cancelled: "已取消", superseded: "已被后续审批替代",
  };
}

export type OverviewQuery = { created_from?: string; created_before?: string; organization_id?: string };
export type MaterialRequestOverview = {
  metric: "current_request_counts"; observed_at: string; created_from: string | null;
  created_before: string | null; organization_id: string | null; matched_requests: number;
  counts: Record<string, Record<string, number>>;
};
const TIMESTAMP = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const invalid = () => new Error("需求概览响应未通过校验，请重新查询");
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw invalid();
  return value as Record<string, unknown>;
}
function exact(row: Record<string, unknown>, keys: string[]): void {
  if (Object.keys(row).length !== keys.length || keys.some(key => !Object.hasOwn(row, key))) throw invalid();
}
function time(value: unknown): number {
  if (typeof value !== "string" || !TIMESTAMP.test(value) || !Number.isFinite(Date.parse(value))) throw invalid();
  return Date.parse(value);
}

export function canReadMaterialRequestOverview(access: AccessContext): boolean {
  return hasFormalPermission(access, "material_request", "read")
    && (hasFormalRole(access, "admin") || hasFormalRole(access, "provincial_manager"));
}

export function parseMaterialRequestOverview(value: unknown, query: OverviewQuery = {}): MaterialRequestOverview {
  const row = object(value);
  exact(row, ["metric", "observed_at", "created_from", "created_before", "organization_id", "matched_requests", "counts"]);
  if (row.metric !== "current_request_counts" || !Number.isSafeInteger(row.matched_requests) || (row.matched_requests as number) < 0) throw invalid();
  time(row.observed_at);
  for (const key of ["created_from", "created_before"] as const) {
    if (query[key] === undefined ? row[key] !== null : time(row[key]) !== time(query[key])) throw invalid();
  }
  if (row.organization_id !== (query.organization_id ?? null)) throw invalid();
  const counts = object(row.counts);
  exact(counts, Object.keys(OVERVIEW_DIMENSIONS));
  for (const [dimension, definition] of Object.entries(OVERVIEW_DIMENSIONS)) {
    const states = object(counts[dimension]);
    exact(states, Object.keys(definition.states));
    let total = 0;
    for (const count of Object.values(states)) {
      if (!Number.isSafeInteger(count) || (count as number) < 0) throw invalid();
      total += count as number;
    }
    if (!Number.isSafeInteger(total) || total !== row.matched_requests) throw invalid();
  }
  return row as MaterialRequestOverview;
}

export async function readMaterialRequestOverview(query: OverviewQuery = {}, signal?: AbortSignal): Promise<MaterialRequestOverview> {
  const search = new URLSearchParams();
  if (query.organization_id !== undefined) {
    if (!UUID.test(query.organization_id)) throw new Error("区域编号无效");
    search.set("organization_id", query.organization_id);
  }
  for (const key of ["created_from", "created_before"] as const) {
    if (query[key] !== undefined) { time(query[key]); search.set(key, query[key]); }
  }
  if (query.created_from && query.created_before && time(query.created_from) >= time(query.created_before)) throw new Error("开始日期不能晚于结束日期");
  const suffix = search.size ? `?${search}` : "";
  return parseMaterialRequestOverview(await api<unknown>(`/v1/reports/material-requests${suffix}`, { cache: "no-store", signal }), query);
}
