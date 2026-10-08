import type { MaterialRequestDetail } from "./formalMaterialRequests";

export const SUPPLY_CAPACITY_FIELDS = ["approved_qty", "cancelled_qty", "allocated_qty", "active_planned_qty",
  "unallocated_qty", "existing_overlap_qty", "new_plan_qty"] as const;
type QuantityField = typeof SUPPLY_CAPACITY_FIELDS[number];
export type SupplyCapacityLine = Readonly<{ request_line_id: string } & Record<QuantityField, string>>;
export type SupplyPlanningCapacity = Readonly<{ schema_version: "1.0"; request_id: string;
  request_version: number; lines: readonly SupplyCapacityLine[] }>;
function invalid(): never { throw new Error("供给计划余量尚未核实或已变化，请重新读取需求"); }
function exact(value: unknown, keys: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)
    || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...keys].sort())) invalid();
  return value as Record<string, unknown>;
}
function units(value: unknown): bigint {
  if (typeof value !== "string" || !/^(?:0|[1-9]\d{0,14})\.\d{3}$/.test(value)) invalid();
  return BigInt(value.replace(".", ""));
}
export function validateSupplyPlanningCapacity(value: unknown, detail: MaterialRequestDetail): SupplyPlanningCapacity {
  const row = exact(value, ["schema_version", "request_id", "request_version", "lines"]);
  if (row.schema_version !== "1.0" || row.request_id !== detail.request_id
    || !Number.isSafeInteger(row.request_version) || row.request_version !== detail.request_version
    || !Array.isArray(row.lines) || row.lines.length !== detail.lines.length
    || !["approved", "partially_approved"].includes(detail.states.request_status)
    || !["not_allocated", "partially_allocated", "allocated"].includes(detail.states.allocation_status)) invalid();
  for (const [key, neutral] of Object.entries({ reservation_status: "not_reserved", outbound_status: "not_started",
    shipment_status: "not_started", logistics_signature_status: "not_signed", oam_receipt_status: "not_occurred",
    personal_inbound_status: "not_started", notification_status: "not_started", reconciliation_status: "not_started" })) {
    if (detail.states[key as keyof typeof detail.states] !== neutral) invalid();
  }
  const seen = new Set<string>();
  const lines = row.lines.map((item) => {
    const line = exact(item, ["request_line_id", ...SUPPLY_CAPACITY_FIELDS]);
    const current = detail.lines.find((candidate) => candidate.request_line_id === line.request_line_id);
    if (!current || seen.has(current.request_line_id)) invalid();
    seen.add(current.request_line_id);
    const q = Object.fromEntries(SUPPLY_CAPACITY_FIELDS.map((key) => [key, units(line[key])])) as Record<QuantityField, bigint>;
    const planned = detail.supply_tasks.filter((task) => task.request_line_id === current.request_line_id
      && !["cancelled", "closed_no_supply"].includes(task.status))
      .reduce((total, task) => total + units(task.original_equivalent_qty), 0n);
    const unallocated = q.approved_qty - q.cancelled_qty - q.allocated_qty;
    const overlap = planned > unallocated ? planned - unallocated : 0n;
    const available = unallocated > planned ? unallocated - planned : 0n;
    if (q.approved_qty !== units(current.final_approved_qty) || q.cancelled_qty !== units(current.cancelled_qty)
      || q.cancelled_qty > q.approved_qty || unallocated < 0n || planned > q.approved_qty - q.cancelled_qty
      || q.active_planned_qty !== planned || q.unallocated_qty !== unallocated
      || q.existing_overlap_qty !== overlap || q.new_plan_qty !== available) invalid();
    return Object.freeze({ ...line }) as SupplyCapacityLine;
  });
  const allocated = lines.reduce((sum, line) => sum + units(line.allocated_qty), 0n);
  const approved = lines.reduce((sum, line) => sum + units(line.approved_qty) - units(line.cancelled_qty), 0n);
  const expectedState = allocated === 0n ? "not_allocated" : allocated === approved ? "allocated" : "partially_allocated";
  if (detail.states.allocation_status !== expectedState) invalid();
  return Object.freeze({ schema_version: "1.0", request_id: detail.request_id,
    request_version: detail.request_version, lines: Object.freeze(lines) });
}
