import { expect, it, vi } from "vitest";
import { createFormalMaterialRequestAdapter } from "./formalMaterialRequestAdapter";
import { preparationPage } from "./materialRequestFulfillmentPreparationTestFixtures";
import { preparationMatchesDetail, validateFulfillmentPreparation } from "./materialRequestFulfillmentPreparation";
import { access, afterReservation } from "./materialRequestReservationTestFixtures";

it.each([false, true])("accepts exact ordinary/SN evidence and binds it to its current demand line (%s)", serial => {
  const page = preparationPage(serial);
  expect(validateFulfillmentPreparation(page)).toEqual(page);
  expect(preparationMatchesDetail(page, afterReservation(), page.request_line_id)).toBe(true);
  expect(preparationMatchesDetail({ ...page, request_version: page.request_version + 1 }, afterReservation(), page.request_line_id)).toBe(false);
  expect(preparationMatchesDetail({ ...page, material_id: page.request_id }, afterReservation(), page.request_line_id)).toBe(false);
});

it.each(["negative", "scientific", "rounding", "over_release", "false_ready", "unknown_reason", "reused_sn", "missing_sn", "too_many", "duplicate", "newer_cursor", "wrong_axis", "naive_time", "fraction_serial", "over_precision", "extra", "zero_id", "fraction_disallowed"])("rejects misleading evidence: %s", kind => {
  const page: any = preparationPage(true), row = page.items[0];
  if (kind === "negative") row.released_qty = "-0.000";
  if (kind === "scientific") row.reserved_qty = "2e0";
  if (kind === "rounding") row.reserved_qty = "2.0001";
  if (kind === "over_release") row.released_qty = "3.000";
  if (kind === "false_ready") row.blockers = ["pool_shortfall"];
  if (kind === "unknown_reason") { row.preparation_status = "blocked"; row.verified_held_qty = "0.000"; row.serials = []; row.blockers = ["unknown"]; }
  if (kind === "reused_sn") row.serials[1] = row.serials[0];
  if (kind === "missing_sn") row.serials.pop();
  if (kind === "too_many") page.items = Array(101).fill(row);
  if (kind === "duplicate") page.items.push(row);
  if (kind === "newer_cursor") row.source_ledger_cursor = page.ledger_cursor + 1;
  if (kind === "wrong_axis") page.state_axes.outbound_status = "picked";
  if (kind === "naive_time") page.projected_at = "2026-09-08T08:00:00";
  if (kind === "fraction_serial") { row.reserved_qty = "2.500"; row.released_qty = "0.500"; }
  if (kind === "over_precision") row.quantity_scale = 4;
  if (kind === "extra") row.outbound_complete = true;
  if (kind === "zero_id") row.reservation_id = "00000000-0000-0000-0000-000000000000";
  if (kind === "fraction_disallowed") { row.tracking_mode = "none"; row.serials = []; row.reserved_qty = "2.500"; row.released_qty = "0.500"; }
  expect(() => validateFulfillmentPreparation(page)).toThrow();
});

it("retains released originals and blocks unavailable evidence without advertising a pick quantity", () => {
  const page: any = preparationPage();
  page.items[0] = { ...page.items[0], preparation_status: "released", released_qty: "2.000", remaining_reserved_qty: "0.000", verified_held_qty: "0.000" };
  expect(validateFulfillmentPreparation(page).items[0].preparation_status).toBe("released");
  page.items[0] = { ...page.items[0], preparation_status: "blocked", released_qty: "1.000", remaining_reserved_qty: "1.000", blockers: ["pool_shortfall"] };
  expect(validateFulfillmentPreparation(page).items[0].verified_held_qty).toBe("0.000");
});

it("uses one exact no-store GET on the no-replay channel and has no write fallback", async () => {
  const page = preparationPage(), principal = access();
  const normal = vi.fn(), noReplay = vi.fn().mockResolvedValue(page);
  const adapter = createFormalMaterialRequestAdapter(principal, normal, noReplay);
  await expect(adapter.listFulfillmentPreparation!(page.request_id, page.request_line_id)).resolves.toEqual(page);
  expect(normal).not.toHaveBeenCalled(); expect(noReplay).toHaveBeenCalledTimes(1);
  expect(noReplay).toHaveBeenCalledWith(`/v1/material-requests/${page.request_id}/fulfillment-preparation?request_line_id=${page.request_line_id}`, expect.objectContaining({ method: "GET", cache: "no-store" }));
  noReplay.mockRejectedValue(new Error("network interrupted"));
  await expect(adapter.listFulfillmentPreparation!(page.request_id, page.request_line_id)).rejects.toThrow("network interrupted");
  expect(noReplay).toHaveBeenCalledTimes(2); expect(normal).not.toHaveBeenCalled();
  const missing = createFormalMaterialRequestAdapter(principal, normal);
  expect(() => missing.listFulfillmentPreparation!(page.request_id, page.request_line_id)).toThrow();
  expect(() => adapter.listFulfillmentPreparation!("../bad", page.request_line_id)).toThrow();
});
