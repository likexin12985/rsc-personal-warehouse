import type { MaterialRequestDetail } from "./formalMaterialRequests";
import { closureObject as object, closureId as id, closureVersion as version, validateCloseInput } from "./materialRequestClosure";
export type ReturnCompensationInput = Readonly<{ expected_request_version: number; reason: string; inbound_id: string;
  inbound_request_hash: string; inbound_plan_hash: string; cancelled_qty: string }>;
export type ReturnCompensation = Readonly<{ schema_version: "1.0"; cancellation_scope: "posted_return_compensation";
  compensation_id: string; inbound_id: string; request_id: string; revision_id: string; request_line_id: string;
  request_version: number; cancelled_qty: string; request_hash: string; evidence_sha256: string; cancelled_at: string; replayed: boolean }>;
export type ReturnCompensationCandidate = Readonly<{ inbound_id: string; request_line_id: string; inbound_request_hash: string;
  inbound_plan_hash: string; quantity: string; sku_code: string; material_name: string; posted_at: string;
  compensate_permitted: boolean; compensation: ReturnCompensation | null }>;
export type ReturnCompensationCandidates = Readonly<{ schema_version: "1.0"; request_id: string; request_version: number; items: readonly ReturnCompensationCandidate[] }>;
export type ReturnCompensationStatus = Readonly<{ lookup_status: "confirmed" | "not_observed"; command: ReturnCompensation | null }>;
function hash(value: unknown) { if (typeof value !== "string" || !/^[0-9a-f]{64}$/.test(value)) throw new Error("退回补偿证据摘要无效"); }
function quantity(value: unknown) { if (typeof value !== "string" || !/^(?:0|[1-9]\d{0,14})\.\d{3}$/.test(value) || BigInt(value.replace(".", "")) <= 0n) throw new Error("退回补偿数量无效"); }
function time(value: unknown) { if (typeof value !== "string" || !/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) throw new Error("退回补偿时间无效"); }
export function validateReturnCompensationInput(value: unknown): ReturnCompensationInput {
  const row = object(value, ["expected_request_version", "reason", "inbound_id", "inbound_request_hash", "inbound_plan_hash", "cancelled_qty"]);
  validateCloseInput({ expected_request_version: row.expected_request_version, reason: row.reason });
  id(row.inbound_id); hash(row.inbound_request_hash); hash(row.inbound_plan_hash); quantity(row.cancelled_qty);
  return row as ReturnCompensationInput;
}
export async function returnCompensationFingerprint(value: ReturnCompensationInput): Promise<string> {
  const input = validateReturnCompensationInput(value);
  const canonical = Object.fromEntries(Object.entries(input).sort(([a], [b]) => a.localeCompare(b)));
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(canonical)));
  return [...new Uint8Array(digest)].map(v => v.toString(16).padStart(2, "0")).join("");
}
export function validateReturnCompensation(value: unknown, detail?: MaterialRequestDetail): ReturnCompensation {
  const row = object(value, ["schema_version", "cancellation_scope", "compensation_id", "inbound_id", "request_id", "revision_id", "request_line_id", "request_version", "cancelled_qty", "request_hash", "evidence_sha256", "cancelled_at", "replayed"]);
  for (const key of ["compensation_id", "inbound_id", "request_id", "revision_id", "request_line_id"]) id(row[key]);
  version(row.request_version); quantity(row.cancelled_qty); hash(row.request_hash); hash(row.evidence_sha256); time(row.cancelled_at);
  if (row.schema_version !== "1.0" || row.cancellation_scope !== "posted_return_compensation" || typeof row.replayed !== "boolean") throw new Error("退回补偿事实无效");
  const result = row as ReturnCompensation;
  if (detail && (result.request_id !== detail.request_id || result.revision_id !== detail.current_revision_id
      || result.request_version > detail.request_version || !detail.lines.some(line => line.request_line_id === result.request_line_id
        && BigInt(line.cancelled_qty.replace(".", "")) >= BigInt(result.cancelled_qty.replace(".", ""))))) throw new Error("补偿记录不属于当前需求或取消总量不一致");
  return result;
}
export function validateReturnCompensationCandidates(value: unknown, detail?: MaterialRequestDetail): ReturnCompensationCandidates {
  const page = object(value, ["schema_version", "request_id", "request_version", "items"]);
  id(page.request_id); if (page.request_version !== 0) version(page.request_version);
  if (page.schema_version !== "1.0" || !Array.isArray(page.items) || page.items.length > 1000
      || (page.request_version === 0 && page.items.length !== 0)
      || (detail && (page.request_id !== detail.request_id || page.request_version !== detail.request_version))) throw new Error("退回入账来源或版本不一致");
  const seen = new Set<string>();
  for (const item of page.items) {
    const row = object(item, ["inbound_id", "request_line_id", "inbound_request_hash", "inbound_plan_hash", "quantity", "sku_code", "material_name", "posted_at", "compensate_permitted", "compensation"]);
    const inbound = id(row.inbound_id); id(row.request_line_id); quantity(row.quantity); hash(row.inbound_request_hash); hash(row.inbound_plan_hash); time(row.posted_at);
    if (seen.has(inbound) || typeof row.compensate_permitted !== "boolean" || typeof row.sku_code !== "string" || !row.sku_code.trim()
        || typeof row.material_name !== "string" || !row.material_name.trim() || (detail && !detail.lines.some(line => line.request_line_id === row.request_line_id))) throw new Error("退回入账来源重复或不完整");
    seen.add(inbound);
    if (row.compensation !== null) {
      const fact = validateReturnCompensation(row.compensation, detail);
      if (row.compensate_permitted || fact.inbound_id !== inbound || fact.request_line_id !== row.request_line_id
          || fact.request_id !== page.request_id || fact.request_version > (page.request_version as number) || fact.cancelled_qty !== row.quantity) throw new Error("退回来源与补偿记录不一致");
    }
  }
  return page as ReturnCompensationCandidates;
}
export function validateReturnCompensationStatus(value: unknown): ReturnCompensationStatus {
  const row = object(value, ["lookup_status", "command"]);
  if (!["confirmed", "not_observed"].includes(String(row.lookup_status)) || (row.lookup_status === "confirmed") !== (row.command !== null)) throw new Error("原补偿查询状态不完整");
  if (row.command !== null) validateReturnCompensation(row.command);
  return row as ReturnCompensationStatus;
}
export function returnCompensationInputFromCandidate(candidate: ReturnCompensationCandidate, detail: MaterialRequestDetail, reason: string): ReturnCompensationInput {
  if (!candidate.compensate_permitted || candidate.compensation !== null || !detail.lines.some(line => line.request_line_id === candidate.request_line_id)) throw new Error("当前退回入账不允许新增补偿");
  return validateReturnCompensationInput({ expected_request_version: detail.request_version, reason, inbound_id: candidate.inbound_id,
    inbound_request_hash: candidate.inbound_request_hash, inbound_plan_hash: candidate.inbound_plan_hash, cancelled_qty: candidate.quantity });
}
