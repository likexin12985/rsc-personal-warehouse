import { afterReservation, access, reservationResult, SERIAL_ID, SERIAL_2_ID } from "./materialRequestReservationTestFixtures";
import { type ReleasePage, type ReleaseResult, type ReleaseSentinel } from "./materialRequestReservationRelease";

export function releasePage(serial = false): ReleasePage {
  const before = afterReservation(), reservation = reservationResult();
  return { schema_version: "1.0", request_id: before.request_id, request_line_id: before.lines[0].request_line_id,
    request_version: before.request_version, revision_id: before.current_revision_id, revision_no: before.current_revision_no, state_axes: before.states,
    items: [{ reservation_id: reservation.reservation_id, reservation_no: reservation.reservation_no, allocation_id: reservation.allocation_id,
      reserved_qty: "2.000", released_qty: "0.000", releasable_qty: "2.000", material_name: "测试物料", sku_code: "SKU-001", location_name: "南京区域仓",
      tracking_mode: serial ? "serial" : "none", quantity_scale: 3, allow_fraction: !serial,
      source_stock_account_id: reservation.stock_account_id, target_stock_account_id: reservation.source_stock_account_id,
      source_balance_version: 1, source_ledger_cursor: 10, serials: serial ? [{ serial_id: SERIAL_ID, serial_no: "SN-A" }, { serial_id: SERIAL_2_ID, serial_no: "SN-B" }] : [] }],
  };
}
export function afterRelease() {
  const result = afterReservation(); result.request_version = 5; result.states.reservation_status = "released"; return result;
}
export function releaseResult(serial = false): ReleaseResult {
  const page = releasePage(serial), item = page.items[0];
  return { schema_version: "1.0", kind: "reservation_release", release_id: "95000000-0000-4000-8000-000000000001", release_no: "RL-001",
    reservation_id: item.reservation_id, allocation_id: item.allocation_id, request_id: page.request_id, request_no: "MR-001", request_line_id: page.request_line_id,
    revision_id: page.revision_id, revision_no: page.revision_no, request_version: 5, current_request_version: 5, released_qty: "2.000", reason: "未使用退回",
    source_stock_account_id: item.source_stock_account_id, target_stock_account_id: item.target_stock_account_id, source_balance_version: item.source_balance_version,
    source_ledger_cursor: item.source_ledger_cursor, release_transaction_id: "96000000-0000-4000-8000-000000000001", release_transaction_no: "INV-REL-001",
    serial_ids: item.serials.map(s => s.serial_id), state_axes: afterRelease().states, idempotency_replayed: false };
}
export function releaseSentinel(serial = false): ReleaseSentinel {
  const page = releasePage(serial), row = page.items[0], principal = access();
  return { v: 1, kind: "reservation_release", trace: "release-test-trace-001", person_id: principal.person_id, authorization_version: principal.authorization_version,
    request_id: page.request_id, request_line_id: page.request_line_id, revision_id: page.revision_id, revision_no: page.revision_no,
    allocation_id: row.allocation_id, source_stock_account_id: row.source_stock_account_id, target_stock_account_id: row.target_stock_account_id, state_axes: page.state_axes,
    input: { expected_request_version: page.request_version, reservation_id: row.reservation_id, released_qty: "2.000", reason: "未使用退回", source_balance_version: row.source_balance_version, source_ledger_cursor: row.source_ledger_cursor, serial_ids: row.serials.map(s => s.serial_id) } };
}
