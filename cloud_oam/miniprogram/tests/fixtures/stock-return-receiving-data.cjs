const id = value => `00000000-0000-4000-8000-${String(value).padStart(12, '0')}`
const expected = { personId: id(1), authorizationVersion: 1, shipmentId: id(2) }
const when = value => `2026-09-13T0${value}:00:00Z`
function parcel(tracked = false) {
  return { verification_status: 'verified', shipment_id: id(2), shipment_no: 'TEST-PARCEL', operation_id: id(3), operation_no: 'TEST-RETURN',
    work_order_id: id(4), sender_person_id: id(5), receiver_person_id: id(1), target_location_id: id(6), target_location_name: '合成接收仓',
    custody_assignment_id: id(7), carrier: '合成承运商', tracking_no: 'TEST-WAYBILL', shipped_at: when(1), recorded_at: when(1),
    lines: [{ shipment_line_id: id(8), outbound_no: 'TEST-DEPARTURE', material_id: id(9), sku_code: 'TEST-SKU', material_name: '合成物料',
      base_unit: '件', condition_code: 'used', lot_id: null, lot_no: null, shipped_quantity: tracked ? '1.000' : '0.375',
      serials: tracked ? [{ serial_id: id(10), serial_no: 'TEST-SN' }] : [] }] }
}
function receipt(tracked = false, type = 'accepted', number = 1) {
  const p = parcel(tracked), original = p.lines[0], q = original.shipped_quantity
  const { outbound_no, shipped_quantity, serials, ...base } = original
  const accepted = ['accepted', 'damaged'].includes(type), rejected = ['rejected', 'wrong_material', 'wrong_serial'].includes(type)
  return { schema_version: '1.0', receipt_id: id(20 + number), receipt_no: `TEST-RECEIPT-${number}`, shipment_id: id(2), operation_id: id(3),
    work_order_id: id(4), operator_person_id: id(1), status: type === 'accepted' ? 'accepted' : 'exception', received_at: when(number + 1), recorded_at: when(number + 1),
    reason: '合成验收', request_id: `wxreq-${String(number).padStart(36, '0')}`, request_hash: 'a'.repeat(64), plan_hash: 'b'.repeat(64),
    target_location_id: id(6), target_custody_assignment_id: id(7), lines: [{ ...base, shipped_qty: q, previously_accepted_qty: '0.000',
      previously_rejected_qty: '0.000', unconfirmed_qty: q, accepted_qty: accepted ? q : '0.000', rejected_qty: rejected ? q : '0.000',
      damaged_qty: type === 'damaged' ? q : '0.000', shortage_qty: type === 'shortage' ? q : '0.000', accepted_serials: accepted ? serials : [],
      rejected_serials: rejected ? serials : [], shortage_serials: type === 'shortage' ? serials : [], damaged_serial_ids: type === 'damaged' ? serials.map(sn => sn.serial_id) : [],
      exceptions: type === 'accepted' ? [] : [{ exception_type: type, description: '合成异常证据', evidence_file_id: id(100 + number) }] }] }
}
function history(tracked = false, receipts = []) {
  const p = parcel(tracked), line = p.lines[0], last = receipts.at(-1), confirmed = last && last.lines[0].shortage_qty === '0.000'
  return { schema_version: '1.0', person_id: id(1), authorization_version: 1, ledger_cursor: 10, queried_at: when(8), package: p, receipts,
    lines: [{ shipment_line_id: id(8), shipped_qty: line.shipped_quantity, accepted_qty: confirmed ? last.lines[0].accepted_qty : '0.000',
      rejected_qty: confirmed ? last.lines[0].rejected_qty : '0.000', damaged_qty: confirmed ? last.lines[0].damaged_qty : '0.000',
      unconfirmed_qty: confirmed ? '0.000' : line.shipped_quantity, unconfirmed_serials: confirmed ? [] : line.serials }] }
}
function directory() { return { schema_version: '1.0', person_id: id(1), authorization_version: 1, ledger_cursor: 10,
  queried_at: when(8), items: [parcel()], next_after_id: null } }
module.exports = { id, when, expected, parcel, receipt, history, directory }
