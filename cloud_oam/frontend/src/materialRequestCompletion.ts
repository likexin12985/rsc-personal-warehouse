import type { MaterialRequestDetail } from "./formalMaterialRequests";

type CoverageLine = Readonly<{
  request_line_id: string; approved_qty: string; cancelled_qty: string;
  posted_qty: string; remaining_qty: string;
}>;
export type MaterialRequestCompletion = Readonly<{
  schema_version: "1.0"; request_id: string; request_version: number; revision_id: string;
  assessment: "final_approved_quantity_coverage"; quantity_coverage_complete: boolean;
  pending_inbound_orders: number; lines: readonly CoverageLine[];
}>;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function invalid(): never { throw new Error("结单核对结果不完整或与当前需求不一致，请重新读取"); }
function object(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)
      || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...keys].sort())) invalid();
  return value as Record<string, unknown>;
}
function id(value: unknown): string {
  if (typeof value !== "string" || !UUID.test(value) || value === "00000000-0000-0000-0000-000000000000") invalid();
  return value;
}
function integer(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) invalid();
  return value;
}
function qty(value: unknown): bigint {
  if (typeof value !== "string" || !/^(?:0|[1-9]\d{0,14})\.\d{3}$/.test(value)) invalid();
  return BigInt(value.replace(".", ""));
}
export function validateMaterialRequestCompletion(value: unknown, detail?: MaterialRequestDetail): MaterialRequestCompletion {
  const row = object(value, ["schema_version", "request_id", "request_version", "revision_id", "assessment",
    "quantity_coverage_complete", "pending_inbound_orders", "lines"]);
  if (row.schema_version !== "1.0" || row.assessment !== "final_approved_quantity_coverage"
      || typeof row.quantity_coverage_complete !== "boolean" || !Array.isArray(row.lines)
      || !row.lines.length || row.lines.length > 200) invalid();
  const lines = row.lines.map(value => {
    const line = object(value, ["request_line_id", "approved_qty", "cancelled_qty", "posted_qty", "remaining_qty"]);
    id(line.request_line_id);
    if (qty(line.approved_qty) !== qty(line.cancelled_qty) + qty(line.posted_qty) + qty(line.remaining_qty)) invalid();
    return Object.freeze(line as CoverageLine);
  });
  if (new Set(lines.map(line => line.request_line_id)).size !== lines.length
      || !lines.some(line => qty(line.approved_qty) > 0n)
      || row.quantity_coverage_complete !== lines.every(line => qty(line.remaining_qty) === 0n)) invalid();
  const result: MaterialRequestCompletion = Object.freeze({ schema_version: "1.0", request_id: id(row.request_id),
    request_version: integer(row.request_version), revision_id: id(row.revision_id),
    assessment: "final_approved_quantity_coverage", quantity_coverage_complete: row.quantity_coverage_complete,
    pending_inbound_orders: integer(row.pending_inbound_orders), lines: Object.freeze(lines) });
  if (detail && (result.request_id !== detail.request_id || result.request_version !== detail.request_version
      || result.revision_id !== detail.current_revision_id || result.lines.length !== detail.lines.length
      || result.lines.some(line => {
        const current = detail.lines.find(item => item.request_line_id === line.request_line_id);
        return !current || qty(current.final_approved_qty) !== qty(line.approved_qty)
          || qty(current.cancelled_qty) !== qty(line.cancelled_qty);
      }))) invalid();
  return result;
}
