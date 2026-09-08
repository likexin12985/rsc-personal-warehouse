import { describe, expect, it } from "vitest";
import { validateMaterialRequestReservationOptionPage as validate } from "./formalMaterialRequestReservationOptions";
import { reservationPage } from "./materialRequestReservationTestFixtures";
describe("strict reservation candidates", () => {
  it("accepts frozen nonserial and serial candidates with material scale", () => {
    expect(Object.isFrozen(validate(reservationPage()).items[0])).toBe(true);
    expect(validate(reservationPage(true)).items[0].serial_options).toHaveLength(2);
    const zeroScale = reservationPage(true); const item = zeroScale.items[0]; item.quantity_scale = 0;
    for (const key of ["quantity", "allocated_qty", "reserved_qty", "remaining_qty", "reservable_qty"]) item[key] = item[key].split(".")[0];
    expect(validate(zeroScale).items[0].reservable_qty).toBe("2");
  });
  it.each(["unknown_page", "unknown_item", "unknown_sn", "missing", "duplicate_allocation", "duplicate_sn", "excess", "arithmetic", "cursor", "material", "unready", "lot", "sn_count", "numeric"])("rejects %s", (kind) => {
    const value = reservationPage(true); const item = value.items[0];
    if (kind === "unknown_page") value.extra = true;
    if (kind === "unknown_item") item.extra = true;
    if (kind === "unknown_sn") item.serial_options[0].extra = true;
    if (kind === "missing") delete item.tracking_mode;
    if (kind === "duplicate_allocation") value.items.push(item);
    if (kind === "duplicate_sn") item.serial_options.push(item.serial_options[0]);
    if (kind === "excess") item.reservable_qty = "3.000";
    if (kind === "arithmetic") item.reserved_qty = "1.000";
    if (kind === "cursor") item.ledger_cursor = 9;
    if (kind === "material") item.material_id = "99000000-0000-4000-8000-000000000001";
    if (kind === "unready") value.opening_balance_status = "pending";
    if (kind === "lot") item.serial_options[0].lot_id = "99000000-0000-4000-8000-000000000001";
    if (kind === "sn_count") item.serial_options.pop();
    if (kind === "numeric") item.reservable_qty = 2;
    expect(() => validate(value)).toThrow();
  });
});
