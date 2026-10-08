import { createFormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
import type { api } from "./api";
import type { MaterialRequestDetail } from "./formalMaterialRequests";
import { expect, it, vi } from "vitest";
import { afterOutbound } from "./materialRequestOutboundTestFixtures";
import { remainingStages, validateMaterialRequestRemainder, validateVersionedMaterialRequestRemainder } from "./materialRequestRemainder";

function fixture() {
  const detail: MaterialRequestDetail = afterOutbound();
  return { schema_version: "1.0", assessment: "remaining_fulfillment_quantities", request_id: detail.request_id,
    revision_id: detail.current_revision_id, request_version: detail.request_version, open_supply_tasks: 0,
    pending_substitutions: 0, lines: detail.lines.map(line => ({ request_line_id: line.request_line_id,
      approved_qty: line.final_approved_qty, cancelled_qty: "0.000", posted_qty: "0.000",
      ...Object.fromEntries(Object.keys(remainingStages).map(key => [key, "0.000"])),
      outbound_unshipped_qty: line.final_approved_qty })) };
}
it("checks each stage against the current approved line", () => {
  const result = validateMaterialRequestRemainder(fixture(), afterOutbound());
  expect(result.lines[0].outbound_unshipped_qty).toBe(afterOutbound().lines[0].final_approved_qty);
  expect(result).not.toHaveProperty("can_cancel");
});
it.each(["duplicate", "missing", "over", "precision", "version", "request", "negative", "permission", "tasks"])("rejects %s", change => {
  const value = fixture();
  if (change === "duplicate") value.lines.push(value.lines[0]);
  if (change === "missing") value.lines = [];
  if (change === "over") value.lines[0].posted_qty = "0.001";
  if (change === "precision") value.lines[0].outbound_unshipped_qty = "1.0001";
  if (change === "version") value.request_version++;
  if (change === "request") value.request_id = "11111111-1111-4111-8111-111111111111";
  if (change === "negative") value.lines[0].outbound_unshipped_qty = "-1.000";
  if (change === "permission") Object.assign(value, { can_cancel: true });
  if (change === "tasks") value.open_supply_tasks = -1;
  expect(() => validateMaterialRequestRemainder(value, afterOutbound())).toThrow();
});
it("keeps subunit remainders exact at numeric18,3 maximum", () => {
  const value = fixture();
  value.lines[0].approved_qty = "999999999999999.999";
  value.lines[0].posted_qty = "999999999999999.998";
  value.lines[0].outbound_unshipped_qty = "0.001";
  expect(validateMaterialRequestRemainder(value).lines[0].outbound_unshipped_qty).toBe("0.001");
});

function returnedFixture() {
  const old = fixture();
  return { ...old, schema_version: "2.0", lines: old.lines.map(line => ({ ...line,
    approved_qty: "3.000", cancelled_qty: "1.000", posted_qty: "1.000", outbound_unshipped_qty: "0.000",
    rejected_unsettled_qty: "0.000", unfulfilled_cancelled_qty: "0.000", return_compensated_qty: "1.000", returned_pending_compensation_qty: "1.000" })) };
}
it("keeps personal inbound, returned pending compensation and each cancellation source distinct", () => {
  const value = returnedFixture();
  const result = validateVersionedMaterialRequestRemainder(value);
  expect(result.schema_version).toBe("2.0");
  expect(result.lines[0]).toMatchObject({ posted_qty: "1.000", return_compensated_qty: "1.000", returned_pending_compensation_qty: "1.000" });
  expect(() => validateMaterialRequestRemainder(value)).toThrow();
  expect(() => validateVersionedMaterialRequestRemainder(value, afterOutbound())).toThrow();
});
it.each(["double-count", "unbound-total", "missing", "schema", "precision", "negative"])("rejects invalid returned remainder: %s", kind => {
  const value = returnedFixture();
  if (kind === "double-count") value.lines[0].rejected_unsettled_qty = "1.000";
  if (kind === "unbound-total") value.lines[0].unfulfilled_cancelled_qty = "1.000";
  if (kind === "missing") delete (value.lines[0] as Partial<typeof value.lines[0]>).return_compensated_qty;
  if (kind === "schema") value.schema_version = "3.0";
  if (kind === "precision") value.lines[0].returned_pending_compensation_qty = "1.0001";
  if (kind === "negative") value.lines[0].return_compensated_qty = "-1.000";
  expect(() => validateVersionedMaterialRequestRemainder(value)).toThrow();
});

it("uses an uncached read and rejects a response for another request", async () => {
  let value = fixture();
  const ordinary = vi.fn(async () => { throw new Error("unexpected request"); });
  const read = vi.fn(async () => value);
  const adapter = createFormalMaterialRequestAdapter({ person_id: "10000000-0000-4000-8000-000000000001", authorization_version: 1 },
    ordinary as unknown as typeof api, read as unknown as typeof api);
  const id = value.request_id;
  await expect(adapter.remainingFulfillment!(id)).resolves.toEqual(value);
  expect(read).toHaveBeenCalledWith(`/v1/material-requests/${id}/remaining-fulfillment`, {
    cache: "no-store", headers: { "Cache-Control": "no-store", Pragma: "no-cache" },
  });
  expect(ordinary).not.toHaveBeenCalled();
  value = { ...value, request_id: "11111111-1111-4111-8111-111111111111" };
  await expect(adapter.remainingFulfillment!(id)).rejects.toThrow("不属于当前需求");
});
