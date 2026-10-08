import type { MaterialRequestDetail } from "./formalMaterialRequests";
import { validateMaterialRequestCompletion, type MaterialRequestCompletion } from "./materialRequestCompletion";

export type CloseInput = Readonly<{ expected_request_version: number; reason: string }>;
export type ClosureResult = Readonly<{ schema_version: "1.0"; closure_id: string; request_id: string;
  revision_id: string; request_version: number; business_status: "closed"; closed_at: string;
  evidence_sha256: string; lines: MaterialRequestCompletion["lines"]; replayed: boolean }>;
export type ClosureState = Readonly<{ request_id: string; request_version: number;
  business_status: "open" | "closed"; close_permitted: boolean; closure: ClosureResult | null }>;
export type ClosureStatus = Readonly<{ lookup_status: "confirmed" | "not_observed"; command: ClosureResult | null }>;
const UUID = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function closureObject(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)
      || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...keys].sort())) throw new Error("关闭结果或原请求字段不完整，请保留记录");
  return value as Record<string, unknown>;
}
export function closureId(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value)) throw new Error("关闭对象编号无效");
  return value;
}
export function closureVersion(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) throw new Error("关闭版本无效");
  return value;
}
export function validateCloseInput(value: unknown): CloseInput {
  const row = closureObject(value, ["expected_request_version", "reason"]);
  if (typeof row.reason !== "string" || !row.reason.length || row.reason.length > 500
      || row.reason !== row.reason.trim() || /[\u0000-\u001f\u007f]/.test(row.reason)) throw new Error("请填写不超过500字的关闭说明");
  return { expected_request_version: closureVersion(row.expected_request_version), reason: row.reason };
}
export async function closeInputFingerprint(value: CloseInput): Promise<string> {
  const input = validateCloseInput(value);
  const bytes = new TextEncoder().encode(JSON.stringify({ expected_request_version: input.expected_request_version, reason: input.reason }));
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(hash)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}
export function validateClosureResult(value: unknown, detail?: MaterialRequestDetail): ClosureResult {
  const row = closureObject(value, ["schema_version", "closure_id", "request_id", "revision_id", "request_version", "business_status", "closed_at", "evidence_sha256", "lines", "replayed"]);
  closureId(row.closure_id);
  if (row.schema_version !== "1.0" || row.business_status !== "closed" || typeof row.replayed !== "boolean"
      || typeof row.evidence_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(row.evidence_sha256)
      || typeof row.closed_at !== "string" || !/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(row.closed_at)
      || !Number.isFinite(Date.parse(row.closed_at))) throw new Error("关闭事实、时间或证据无效");
  validateMaterialRequestCompletion({ schema_version: "1.0", request_id: row.request_id, revision_id: row.revision_id,
    request_version: row.request_version, assessment: "final_approved_quantity_coverage", quantity_coverage_complete: true,
    pending_inbound_orders: 0, lines: row.lines }, detail);
  return row as ClosureResult;
}
export function validateClosureState(value: unknown, detail?: MaterialRequestDetail): ClosureState {
  const row = closureObject(value, ["request_id", "request_version", "business_status", "close_permitted", "closure"]);
  closureId(row.request_id);
  if (typeof row.request_version !== "number" || !Number.isSafeInteger(row.request_version) || row.request_version < 0) throw new Error("关闭状态版本无效");
  if (typeof row.close_permitted !== "boolean" || !["open", "closed"].includes(String(row.business_status))
      || (row.business_status === "closed") !== (row.closure !== null)) throw new Error("关闭状态缺少事实依据");
  if (detail && (row.request_id !== detail.request_id || row.request_version !== detail.request_version)) throw new Error("关闭状态不属于当前需求版本");
  if (row.closure !== null) {
    const closed = validateClosureResult(row.closure, detail);
    if (row.close_permitted || closed.request_id !== row.request_id || closed.request_version !== row.request_version) throw new Error("关闭状态与事实不一致");
  }
  return row as ClosureState;
}
export function validateClosureStatus(value: unknown): ClosureStatus {
  const row = closureObject(value, ["lookup_status", "command"]);
  if (!["confirmed", "not_observed"].includes(String(row.lookup_status))
      || (row.lookup_status === "confirmed") !== (row.command !== null)) throw new Error("原关闭请求尚不能确认");
  if (row.command !== null && !validateClosureResult(row.command).replayed) throw new Error("原关闭请求回读无效");
  return row as ClosureStatus;
}
