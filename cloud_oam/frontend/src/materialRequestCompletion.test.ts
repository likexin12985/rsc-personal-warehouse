import { describe, expect, it } from "vitest";
import { afterOutbound } from "./materialRequestOutboundTestFixtures";
import { validateMaterialRequestCompletion } from "./materialRequestCompletion";
import type { MaterialRequestDetail } from "./formalMaterialRequests";

export function completionFixture(complete = false) {
  const detail: MaterialRequestDetail = afterOutbound();
  return { schema_version: "1.0", request_id: detail.request_id, request_version: detail.request_version,
    revision_id: detail.current_revision_id, assessment: "final_approved_quantity_coverage",
    quantity_coverage_complete: complete, pending_inbound_orders: 0,
    lines: detail.lines.map(line => ({ request_line_id: line.request_line_id,
      approved_qty: line.final_approved_qty, cancelled_qty: "0.000",
      posted_qty: complete ? line.final_approved_qty : "0.000",
      remaining_qty: complete ? "0.000" : line.final_approved_qty })) };
}

it("keeps quantity coverage distinct from closure", () => {
  const result = validateMaterialRequestCompletion(completionFixture(true), afterOutbound());
  expect(result.quantity_coverage_complete).toBe(true);
  expect(result).not.toHaveProperty("can_close");
});

describe("rejects incomplete or unrelated coverage", () => {
  it.each(["version", "request", "line", "duplicate", "over", "missing", "summary", "precision", "unknown", "projection"])("rejects %s", change => {
    const value = completionFixture();
    if (change === "version") value.request_version++;
    if (change === "request") value.request_id = "11111111-1111-4111-8111-111111111111";
    if (change === "line") value.lines[0].request_line_id = "11111111-1111-4111-8111-111111111111";
    if (change === "duplicate") value.lines.push(value.lines[0]);
    if (change === "over") value.lines[0].posted_qty = value.lines[0].approved_qty;
    if (change === "missing") value.lines = [];
    if (change === "summary") value.quantity_coverage_complete = true;
    if (change === "precision") value.lines[0].remaining_qty = "1.0001";
    if (change === "unknown") Object.assign(value, { can_close: true });
    if (change === "projection") value.lines[0] = { ...value.lines[0], approved_qty: "0.001", remaining_qty: "0.001" };
    expect(() => validateMaterialRequestCompletion(value, afterOutbound())).toThrow();
  });
});

it("uses exact large decimal quantities beyond safe integer precision", () => {
  const value = completionFixture();
  value.lines[0] = { ...value.lines[0], approved_qty: "999999999999999.999", posted_qty: "999999999999999.998", remaining_qty: "0.001" };
  expect(validateMaterialRequestCompletion(value).lines[0].remaining_qty).toBe("0.001");
});
