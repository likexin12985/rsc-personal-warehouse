import type { MaterialRequestDetail } from "./formalMaterialRequests";
import { closureObject as object, closureId as id, closureVersion as version, validateCloseInput } from "./materialRequestClosure";
import { remainingStages, validateVersionedMaterialRequestRemainder } from "./materialRequestRemainder";
export type RemainingCancelLine = Readonly<{ request_line_id: string; cancelled_qty: string }>;
export type RemainingCancelInput = Readonly<{ expected_request_version: number; reason: string; lines: readonly RemainingCancelLine[] }>;
export type RemainingCancellation = Readonly<{ schema_version: "1.0"; cancellation_id: string; request_id: string;
  revision_id: string; request_version: number; cancellation_scope: "all_remaining_unfulfilled"; cancelled_at: string;
  evidence_sha256: string; lines: readonly RemainingCancelLine[]; replayed: boolean }>;
export type RemainingCancellationState = Readonly<{ request_id: string; request_version: number; cancel_permitted: boolean; cancellation: RemainingCancellation | null }>;
export type RemainingCancellationStatus = Readonly<{ lookup_status: "confirmed" | "not_observed"; command: RemainingCancellation | null }>;
function lines(value: unknown): readonly RemainingCancelLine[] {
  if (!Array.isArray(value) || !value.length || value.length > 200) throw new Error("取消明细不完整");
  const result = value.map(item => {
    const row = object(item, ["request_line_id", "cancelled_qty"]);
    if (typeof row.cancelled_qty !== "string" || !/^(?:0|[1-9]\d{0,14})\.\d{3}$/.test(row.cancelled_qty)
      || BigInt(row.cancelled_qty.replace(".", "")) <= 0n) throw new Error("取消数量必须为准确的正数");
    return { request_line_id: id(row.request_line_id), cancelled_qty: row.cancelled_qty };
  });
  if (new Set(result.map(row => row.request_line_id)).size !== result.length) throw new Error("取消明细重复");
  return result;
}
export function validateRemainingCancelInput(value: unknown): RemainingCancelInput {
  const row = object(value, ["expected_request_version", "reason", "lines"]);
  return { ...validateCloseInput({ expected_request_version: row.expected_request_version, reason: row.reason }), lines: lines(row.lines) };
}
export function cancellationInputFromRemainder(value: unknown, detail: MaterialRequestDetail, reason: string): RemainingCancelInput {
  const remaining = validateVersionedMaterialRequestRemainder(value, detail);
  const priorCancellation = remaining.schema_version === "2.0"
    ? remaining.lines.some(line => line.unfulfilled_cancelled_qty !== "0.000" || line.returned_pending_compensation_qty !== "0.000")
    : remaining.lines.some(line => line.cancelled_qty !== "0.000");
  if (remaining.open_supply_tasks || remaining.pending_substitutions || priorCancellation || remaining.lines.some(line =>
      Object.keys(remainingStages).filter(key => key !== "unreserved_qty").some(key => line[key as keyof typeof remainingStages] !== "0.000"))) {
    throw new Error("请先处理占用、在途、待入账、拒收和开放供给任务");
  }
  const cancellable = remaining.lines.filter(line => line.unreserved_qty !== "0.000");
  if (!cancellable.length) throw new Error("没有待取消的剩余数量，业务关闭另行确认。");
  return validateRemainingCancelInput({ expected_request_version: detail.request_version, reason,
    lines: cancellable.map(line => ({ request_line_id: line.request_line_id, cancelled_qty: line.unreserved_qty })) });
}
export async function remainingCancelFingerprint(value: RemainingCancelInput): Promise<string> {
  const input = validateRemainingCancelInput(value);
  const canonical = { expected_request_version: input.expected_request_version,
    lines: input.lines.map(line => ({ cancelled_qty: line.cancelled_qty, request_line_id: line.request_line_id })), reason: input.reason };
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(canonical)));
  return [...new Uint8Array(digest)].map(v => v.toString(16).padStart(2, "0")).join("");
}
export function validateRemainingCancellation(value: unknown, detail?: MaterialRequestDetail): RemainingCancellation {
  const row = object(value, ["schema_version", "cancellation_id", "request_id", "revision_id", "request_version", "cancellation_scope", "cancelled_at", "evidence_sha256", "lines", "replayed"]);
  id(row.cancellation_id); id(row.request_id); id(row.revision_id); version(row.request_version);
  const checked = lines(row.lines);
  if (row.schema_version !== "1.0" || row.cancellation_scope !== "all_remaining_unfulfilled" || typeof row.replayed !== "boolean"
      || typeof row.evidence_sha256 !== "string" || !/^[a-f0-9]{64}$/.test(row.evidence_sha256)
      || typeof row.cancelled_at !== "string" || !/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(row.cancelled_at) || !Number.isFinite(Date.parse(row.cancelled_at))) throw new Error("取消事实或证据无效");
  if (detail && (row.request_id !== detail.request_id || row.revision_id !== detail.current_revision_id || row.request_version !== detail.request_version
    || checked.some(line => !detail.lines.some(current => current.request_line_id === line.request_line_id
      && BigInt(current.final_approved_qty.replace(".", "")) >= BigInt(line.cancelled_qty.replace(".", "")))))) throw new Error("取消记录不属于当前需求或数量超限");
  return row as RemainingCancellation;
}
export function validateRemainingCancellationState(value: unknown, detail?: MaterialRequestDetail): RemainingCancellationState {
  const row = object(value, ["request_id", "request_version", "cancel_permitted", "cancellation"]);
  id(row.request_id);
  if (typeof row.request_version !== "number" || !Number.isSafeInteger(row.request_version) || row.request_version < 0 || typeof row.cancel_permitted !== "boolean") throw new Error("取消状态无效");
  if (detail && (row.request_id !== detail.request_id || row.request_version !== detail.request_version)) throw new Error("取消状态版本已变化");
  if (row.cancellation !== null) {
    const result = validateRemainingCancellation(row.cancellation, detail);
    if (row.cancel_permitted || result.request_id !== row.request_id || result.request_version !== row.request_version) throw new Error("取消状态与事实不一致");
  }
  return row as RemainingCancellationState;
}
export function validateRemainingCancellationStatus(value: unknown): RemainingCancellationStatus {
  const row = object(value, ["lookup_status", "command"]);
  if (!["confirmed", "not_observed"].includes(String(row.lookup_status)) || (row.lookup_status === "confirmed") !== (row.command !== null)) throw new Error("原取消结果尚不能确认");
  if (row.command !== null && !validateRemainingCancellation(row.command).replayed) throw new Error("原取消回读无效");
  return row as RemainingCancellationStatus;
}
