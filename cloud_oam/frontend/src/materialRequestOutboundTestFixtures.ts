import { access } from "./materialRequestReservationTestFixtures";
import { afterPick, pickResult, pickPage } from "./materialRequestPickTestFixtures";
import { type OutboundPage, type OutboundResult, type OutboundSentinel } from "./materialRequestOutbound";

export function outboundPage(serial = false): OutboundPage {
  const before = afterPick(), original = pickResult(serial), item = pickPage(serial).items[0];
  return { schema_version: "1.0", request_id: before.request_id, request_line_id: before.lines[0].request_line_id,
    request_version: before.request_version, revision_id: before.current_revision_id, revision_no: before.current_revision_no, state_axes: before.states,
    items: [{ pick_id: original.pick_id, pick_no: original.pick_no, outbound_line_id: original.outbound_line_id,
      outbound_id: original.outbound_id, outbound_no: original.outbound_no,
      reservation_id: original.reservation_id, allocation_id: original.allocation_id,
      picked_qty: "2.000", outbound_qty: "0.000", outboundable_qty: "2.000", last_outbound_at: null,
      condition_code: "new", lot_no: null, material_name: item.material_name, sku_code: item.sku_code, location_name: item.location_name,
      tracking_mode: item.tracking_mode, quantity_scale: 3, allow_fraction: !serial,
      source_stock_account_id: original.target_stock_account_id, target_stock_account_id: "88000000-0000-4000-8000-000000000001",
      source_balance_version: 1, source_ledger_cursor: 11, serials: item.serials }],
  };
}
export function afterOutbound() {
  const result = afterPick(); result.request_version = 6; result.states.outbound_status = "outbound"; return result;
}
export function outboundResult(serial = false): OutboundResult {
  const page = outboundPage(serial), item = page.items[0];
  return { schema_version: "1.0", kind: "outbound", outbound_line_id: item.outbound_line_id, pick_id: item.pick_id,
    posting_id: "85000000-0000-4000-8000-000000000001", posting_no: "OUT-001",
    reservation_id: item.reservation_id, allocation_id: item.allocation_id, request_id: page.request_id, request_no: "MR-001", request_line_id: page.request_line_id,
    revision_id: page.revision_id, revision_no: page.revision_no, request_version: 6, current_request_version: 6, outbound_qty: "2.000", reason: "实物核对一致",
    source_stock_account_id: item.source_stock_account_id, target_stock_account_id: item.target_stock_account_id, source_balance_version: item.source_balance_version,
    source_ledger_cursor: item.source_ledger_cursor, outbound_transaction_id: "86000000-0000-4000-8000-000000000001", outbound_transaction_no: "INV-OUT-001",
    serial_ids: item.serials.map(s => s.serial_id), state_axes: afterOutbound().states, idempotency_replayed: false };
}
export function outboundSentinel(serial = false): OutboundSentinel {
  const page = outboundPage(serial), row = page.items[0], principal = access();
  return { v: 1, kind: "outbound", trace: "outbound-test-trace-001", person_id: principal.person_id, authorization_version: principal.authorization_version,
    request_id: page.request_id, request_line_id: page.request_line_id, revision_id: page.revision_id, revision_no: page.revision_no,
    allocation_id: row.allocation_id, source_stock_account_id: row.source_stock_account_id, target_stock_account_id: row.target_stock_account_id, state_axes: page.state_axes,
    input: { expected_request_version: page.request_version, pick_id: row.pick_id, target_stock_account_id: row.target_stock_account_id,
      outbound_qty: "2.000", reason: "实物核对一致", source_balance_version: row.source_balance_version, source_ledger_cursor: row.source_ledger_cursor, serial_ids: row.serials.map(s => s.serial_id) } };
}
