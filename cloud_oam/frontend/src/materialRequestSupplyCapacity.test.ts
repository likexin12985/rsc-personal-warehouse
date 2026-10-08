import { describe, expect, it } from "vitest";
import type { MaterialRequestDetail } from "./formalMaterialRequests";
import { validateSupplyPlanningCapacity } from "./materialRequestSupplyCapacity";
function fixture() {
  const detail = { request_id: "r", request_version: 9, states: { request_status: "approved",
    allocation_status: "partially_allocated", reservation_status: "not_reserved", outbound_status: "not_started",
    shipment_status: "not_started", logistics_signature_status: "not_signed", oam_receipt_status: "not_occurred",
    personal_inbound_status: "not_started", notification_status: "not_started", reconciliation_status: "not_started" },
    lines: [{ request_line_id: "line", final_approved_qty: "4.000", cancelled_qty: "0.000" }],
    supply_tasks: [{ request_line_id: "line", status: "open", original_equivalent_qty: "1.000" }],
  } as unknown as MaterialRequestDetail;
  const value = { schema_version: "1.0", request_id: "r", request_version: 9,
    lines: [{ request_line_id: "line", approved_qty: "4.000", cancelled_qty: "0.000", allocated_qty: "2.000",
      active_planned_qty: "1.000", unallocated_qty: "2.000", existing_overlap_qty: "0.000", new_plan_qty: "1.000" }] };
  return { detail, value };
}
describe("verified planning capacity", () => {
  it("separates remaining allocation and active planned quantity", () => {
    const { detail, value } = fixture();
    expect(validateSupplyPlanningCapacity(value, detail).lines[0].new_plan_qty).toBe("1.000");
  });
  it("retains historical overlap without silently consuming an old plan", () => {
    const { detail, value } = fixture();
    value.lines[0] = { ...value.lines[0], allocated_qty: "3.500", unallocated_qty: "0.500",
      existing_overlap_qty: "0.500", new_plan_qty: "0.000" };
    expect(validateSupplyPlanningCapacity(value, detail).lines[0].existing_overlap_qty).toBe("0.500");
  });
  it.each(["stale", "extra", "missing", "duplicate", "wrong_quantity", "number", "overallocated", "axis", "later"])("rejects %s rather than guessing a limit", (kind) => {
    const { detail, value } = fixture();
    if (kind === "stale") value.request_version--;
    if (kind === "extra") (value as any).allowed = true;
    if (kind === "missing") value.lines = [];
    if (kind === "duplicate") value.lines.push(value.lines[0]);
    if (kind === "wrong_quantity") value.lines[0].new_plan_qty = "2.000";
    if (kind === "number") (value.lines[0] as any).allocated_qty = 2;
    if (kind === "overallocated") value.lines[0].allocated_qty = "5.000";
    if (kind === "axis") (detail.states as any).allocation_status = "allocated";
    if (kind === "later") (detail.states as any).reservation_status = "released";
    expect(() => validateSupplyPlanningCapacity(value, detail)).toThrow("尚未核实");
  });
});
