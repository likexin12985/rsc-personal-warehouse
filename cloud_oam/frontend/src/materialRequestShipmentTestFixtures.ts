import { afterOutbound, outboundResult, outboundPage } from "./materialRequestOutboundTestFixtures";
import { access } from "./materialRequestReservationTestFixtures";
import type { ShipmentInput, ShipmentResult, ShipmentOptions } from "./materialRequestShipment";
import type { ShipmentSentinel } from "./materialRequestShipmentRecovery";
export const ID = (n: number) => `a1000000-0000-4000-8000-${String(n).padStart(12, "0")}`;
export function shipmentPage(serial = false): ShipmentOptions {
  const out = outboundResult(serial), row = outboundPage(serial).items[0];
  return { request_id: out.request_id, request_version: 6, items: [{ posting_id: out.posting_id, posting_no: out.posting_no, outbound_line_id: out.outbound_line_id,
    pick_id: out.pick_id, posted_qty: "2.000", shipped_qty: "0.000", shippable_qty: "2.000", serial_ids: out.serial_ids, serials: row.serials }] };
}
export function shipmentInput(serial = false): ShipmentInput {
  return { expected_request_version: 6, target_location_id: ID(1), target_person_id: ID(2), carrier: "人工承运", tracking_no: "TRACK-01", shipped_at: "2026-09-11T10:00:00.000Z",
    lines: [{ outbound_posting_id: outboundResult().posting_id, shipped_qty: "1.000", serial_ids: serial ? shipmentPage(true).items[0].serial_ids.slice(0, 1) : [] }] };
}
export function shipmentResult(input = shipmentInput()): ShipmentResult {
  return { schema_version: "1.0", shipment_id: ID(3), shipment_no: "SHP-001", request_id: afterOutbound().request_id, status: "shipped", target_location_id: input.target_location_id,
    target_person_id: input.target_person_id, carrier: input.carrier, tracking_no: input.tracking_no, shipped_at: input.shipped_at,
    lines: input.lines.map((line, i) => ({ ...line, shipment_line_id: ID(10 + i) })), idempotency_replayed: false };
}
export function shipmentSentinel(input = shipmentInput()): ShipmentSentinel {
  return { v: 1, kind: "shipment", person_id: access().person_id, authorization_version: access().authorization_version, request_id: afterOutbound().request_id,
    trace: "shipment-trace-test-001", key: "shipment-key-test-001", input };
}
