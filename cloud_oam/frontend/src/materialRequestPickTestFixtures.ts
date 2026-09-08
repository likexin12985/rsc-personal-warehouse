import { afterReservation, access, reservationResult, SERIAL_ID, SERIAL_2_ID } from "./materialRequestReservationTestFixtures";
import { type PickPage, type PickResult, type PickSentinel } from "./materialRequestReservationPick";

export function pickPage(serial = false): PickPage {
  const before = afterReservation(), reservation = reservationResult();
  return { schema_version: "1.0", request_id: before.request_id, request_line_id: before.lines[0].request_line_id,
    request_version: before.request_version, revision_id: before.current_revision_id, revision_no: before.current_revision_no, state_axes: before.states,
    items: [{ reservation_id: reservation.reservation_id, reservation_no: reservation.reservation_no, allocation_id: reservation.allocation_id,
      reserved_qty: "2.000", released_qty: "0.000", condition_code: "new", lot_no: null, picked_qty: "0.000", pickable_qty: "2.000", material_name: "测试物料", sku_code: "SKU-001", location_name: "南京区域仓",
      tracking_mode: serial ? "serial" : "none", quantity_scale: 3, allow_fraction: !serial,
      source_stock_account_id: reservation.stock_account_id, target_stock_account_id: "98000000-0000-4000-8000-000000000001",
      source_balance_version: 1, source_ledger_cursor: 10, serials: serial ? [{ serial_id: SERIAL_ID, serial_no: "SN-A" }, { serial_id: SERIAL_2_ID, serial_no: "SN-B" }] : [] }],
  };
}
export function afterPick() {
  const result = afterReservation(); result.request_version = 5; result.states.outbound_status = "picked"; return result;
}
export function pickResult(serial = false): PickResult {
  const page = pickPage(serial), item = page.items[0];
  return { schema_version: "1.0", kind: "reservation_pick", outbound_line_id: "97000000-0000-4000-8000-000000000001", outbound_id: "95000000-0000-4000-8000-000000000001", outbound_no: "OB-001", pick_id: "95000000-0000-4000-8000-000000000001", pick_no: "PK-001",
    reservation_id: item.reservation_id, allocation_id: item.allocation_id, request_id: page.request_id, request_no: "MR-001", request_line_id: page.request_line_id,
    revision_id: page.revision_id, revision_no: page.revision_no, request_version: 5, current_request_version: 5, picked_qty: "2.000", reason: "实物核对一致",
    source_stock_account_id: item.source_stock_account_id, target_stock_account_id: item.target_stock_account_id, source_balance_version: item.source_balance_version,
    source_ledger_cursor: item.source_ledger_cursor, pick_transaction_id: "96000000-0000-4000-8000-000000000001", pick_transaction_no: "INV-PICK-001",
    serial_ids: item.serials.map(s => s.serial_id), state_axes: afterPick().states, idempotency_replayed: false };
}
export function pickSentinel(serial = false): PickSentinel {
  const page = pickPage(serial), row = page.items[0], principal = access();
  return { v: 1, kind: "reservation_pick", trace: "pick-test-trace-001", person_id: principal.person_id, authorization_version: principal.authorization_version,
    request_id: page.request_id, request_line_id: page.request_line_id, revision_id: page.revision_id, revision_no: page.revision_no,
    allocation_id: row.allocation_id, source_stock_account_id: row.source_stock_account_id, target_stock_account_id: row.target_stock_account_id, state_axes: page.state_axes,
    input: { expected_request_version: page.request_version, reservation_id: row.reservation_id, picked_qty: "2.000", reason: "实物核对一致", source_balance_version: row.source_balance_version, source_ledger_cursor: row.source_ledger_cursor, serial_ids: row.serials.map(s => s.serial_id) } };
}
