import type { MaterialRequestDetail } from "./formalMaterialRequests";
import { validateMaterialRequestCompletion } from "./materialRequestCompletion";

export const remainingStages = {
  unreserved_qty: "未占用（含已释放）", reserved_unpicked_qty: "已占用待拣货",
  picked_unoutbound_qty: "已拣货待出库", outbound_unshipped_qty: "已出库待交运",
  shipped_unreceived_qty: "已发运待验收", accepted_unposted_qty: "已验收待入账",
  rejected_unsettled_qty: "拒收待处理",
} as const;
type Stage = keyof typeof remainingStages;
type Line = Readonly<Record<Stage, string> & { request_line_id: string; approved_qty: string; cancelled_qty: string; posted_qty: string }>;
export type MaterialRequestRemainder = Readonly<{ schema_version: "1.0"; assessment: "remaining_fulfillment_quantities";
  request_id: string; revision_id: string; request_version: number; open_supply_tasks: number;
  pending_substitutions: number; lines: readonly Line[] }>;
type ReturnedLine = Line & Readonly<{ unfulfilled_cancelled_qty: string; return_compensated_qty: string;
  returned_pending_compensation_qty: string }>;
export type ReturnedMaterialRequestRemainder = Omit<MaterialRequestRemainder, "schema_version" | "lines"> &
  Readonly<{ schema_version: "2.0"; lines: readonly ReturnedLine[] }>;
export type VersionedMaterialRequestRemainder = MaterialRequestRemainder | ReturnedMaterialRequestRemainder;
function invalid(): never { throw new Error("剩余履约数量不完整或与当前需求不一致，请重新读取"); }
function record(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)
    || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(keys.sort())) invalid();
  return value as Record<string, unknown>;
}
function quantity(value: unknown): bigint {
  if (typeof value !== "string" || !/^(?:0|[1-9]\d{0,14})\.\d{3}$/.test(value)) invalid();
  return BigInt(value.replace(".", ""));
}
export function validateMaterialRequestRemainder(value: unknown, detail?: MaterialRequestDetail): MaterialRequestRemainder {
  const result = validateVersionedMaterialRequestRemainder(value, detail);
  // Original cancellation seals must retain their exact seven-stage contract.
  if (result.schema_version !== "1.0") invalid();
  return result;
}
export function validateVersionedMaterialRequestRemainder(value: unknown, detail?: MaterialRequestDetail): VersionedMaterialRequestRemainder {
  const row = record(value, ["schema_version", "assessment", "request_id", "revision_id", "request_version", "open_supply_tasks", "pending_substitutions", "lines"]);
  if (!["1.0", "2.0"].includes(row.schema_version as string)
    || row.assessment !== "remaining_fulfillment_quantities" || !Array.isArray(row.lines)) invalid();
  const returned = row.schema_version === "2.0";
  for (const key of ["open_supply_tasks", "pending_substitutions"]) {
    if (typeof row[key] !== "number" || !Number.isSafeInteger(row[key]) || (row[key] as number) < 0) invalid();
  }
  const lines = row.lines.map(value => record(value, ["request_line_id", "approved_qty", "cancelled_qty", "posted_qty", ...Object.keys(remainingStages),
    ...(returned ? ["unfulfilled_cancelled_qty", "return_compensated_qty", "returned_pending_compensation_qty"] : [])]));
  const coverage = lines.map(line => {
    if (returned && quantity(line.cancelled_qty) !== quantity(line.unfulfilled_cancelled_qty) + quantity(line.return_compensated_qty)) invalid();
    const sum = Object.keys(remainingStages).reduce((sum, key) => sum + quantity(line[key]),
      returned ? quantity(line.returned_pending_compensation_qty) : 0n);
    const remaining = `${sum / 1000n}.${(sum % 1000n).toString().padStart(3, "0")}`;
    return { request_line_id: line.request_line_id, approved_qty: line.approved_qty, cancelled_qty: line.cancelled_qty,
      posted_qty: line.posted_qty, remaining_qty: remaining };
  });
  validateMaterialRequestCompletion({ schema_version: "1.0", request_id: row.request_id,
    revision_id: row.revision_id, request_version: row.request_version, assessment: "final_approved_quantity_coverage",
    pending_inbound_orders: 0, quantity_coverage_complete: coverage.every(line => line.remaining_qty === "0.000"), lines: coverage }, detail);
  return Object.freeze({ ...row, lines: Object.freeze(lines.map(line => Object.freeze({ ...line }))) }) as VersionedMaterialRequestRemainder;
}
