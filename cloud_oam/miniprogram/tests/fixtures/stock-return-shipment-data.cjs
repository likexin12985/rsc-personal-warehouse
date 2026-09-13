const out = require('./stock-return-outbound-data.cjs')
const returns = require('./stock-return-data.cjs')
const c = require('../../utils/stock-return-shipment-contract')
const { amount } = require('../../utils/stock-return-contract')
const { fromUnits } = require('../../utils/my-receipt-command')
const { id } = out
const at = '2026-09-13T01:02:00.123456Z', now = '2026-09-13T01:03:00Z'
function input(tracked = false) { const parent = out.input(tracked); return { workOrderId: parent.workOrderId, personId: parent.personId,
  authorizationVersion: parent.authorizationVersion, operationId: parent.operationId, shippedAt: at, carrier: '人工承运商🔧', trackingNo: 'SYNTHETIC-PARCEL-1',
  reason: '已交承运\n等待接收仓独立验收', lines: [{ outbound_line_id: id(40), quantity: '1.000', serial_ids: parent.lines[0].serial_verifications.map(sn => sn.serial_id) }] } }
function departures(value = input(), items, cancelled = false) { const parent = out.input(!!value.lines[0].serial_ids.length)
  return out.history(parent, items === undefined ? [out.result(parent)] : items, cancelled) }
function history(value = input(), items = [], source = departures(value)) { return { schema_version: '1.0', operation_id: value.operationId,
  work_order_id: value.workOrderId, person_id: value.personId, authorization_version: value.authorizationVersion, queried_at: now,
  shipment_status: items.length ? items.reduce((total, item) => total + amount(item.lines[0].selected_quantity), 0n) === 1000n ? 'shipped' : 'partially_shipped' : 'not_shipped',
  departures: source, items } }
function options(value = input(), items = [], source = departures(value)) {
  const batch = source.items[0], origin = batch && batch.lines[0]
  const selected = items.flatMap(item => item.lines), shipped = selected.reduce((sum, row) => sum + amount(row.selected_quantity), 0n)
  const used = new Set(selected.flatMap(row => row.selected_serials.map(sn => sn.serial_id)))
  const row = origin ? { outbound_id: batch.outbound_id, outbound_no: batch.outbound_no, outbound_at: batch.outbound_at,
    outbound_line_id: id(40), operation_line_id: origin.operation_line_id, source_recovery_line_id: origin.source_recovery_line_id,
    transit_stock_account_id: id(41), material_id: origin.material_id, sku_code: origin.sku_code, material_name: origin.material_name,
    base_unit: origin.base_unit, condition_code: origin.condition_code, lot_id: origin.lot_id, lot_no: origin.lot_no,
    tracking_mode: origin.selected_serials.length ? 'serial' : 'none', quantity_scale: 3, allow_fraction: !origin.selected_serials.length,
    outbound_quantity: origin.selected_quantity, shipped_quantity: fromUnits(shipped), unshipped_quantity: fromUnits(amount(origin.selected_quantity) - shipped),
    in_transit_quantity: origin.selected_quantity, unassigned_quantity: fromUnits(amount(origin.selected_quantity) - shipped),
    selectable_quantity: fromUnits(amount(origin.selected_quantity) - shipped), serials: origin.selected_serials.filter(sn => !used.has(sn.serial_id)) } : null
  return { schema_version: '1.0', operation_id: value.operationId, operation_no: source.original.operation_no, work_order_id: value.workOrderId,
    person_id: value.personId, authorization_version: value.authorizationVersion, ledger_cursor: 10, queried_at: now, destination: returns.destination(), lines: row ? [row] : [] }
}
function preview(value = input()) {
  const choices = options(value), row = { ...choices.lines[0] }
  for (const key of ['outbound_at', 'tracking_mode', 'quantity_scale', 'allow_fraction', 'selectable_quantity', 'serials']) delete row[key]
  row.selected_quantity = value.lines[0].quantity
  row.selected_serials = choices.lines[0].serials.filter(sn => value.lines[0].serial_ids.includes(sn.serial_id))
  return { schema_version: '1.0', planning_status: 'preview_only', operation_id: value.operationId, operation_no: choices.operation_no,
    work_order_id: value.workOrderId, operator_person_id: value.personId, authorization_version: value.authorizationVersion,
    shipped_at: value.shippedAt, carrier: value.carrier.trim(), tracking_no: value.trackingNo.trim(), reason: value.reason, ledger_cursor: 10,
    checked_at: now, destination: choices.destination, request_hash: c.requestHash(value), plan_hash: 'a'.repeat(64), lines: [row] }
}
function marker(value = input()) { return { v: 1, kind: c.KIND, work_order_id: value.workOrderId, person_id: value.personId,
  authorization_version: value.authorizationVersion, operation_type: c.ACTION, trace_request_id: 'wxreq-' + 'e'.repeat(36),
  request_hash: c.requestHash(value), plan_hash: 'a'.repeat(64), operation_id: value.operationId } }
function result(value = input(), request = marker(value)) { return { schema_version: '1.0', status: 'shipped', shipment_id: id(42), shipment_no: 'RET-SHIP-TEST',
  operation_id: value.operationId, work_order_id: value.workOrderId, operator_person_id: value.personId, shipped_at: value.shippedAt,
  recorded_at: now, carrier: value.carrier.trim(), tracking_no: value.trackingNo.trim(), reason: value.reason, request_id: request.trace_request_id,
  request_hash: request.request_hash, plan_hash: request.plan_hash, destination: returns.destination(), lines: preview(value).lines } }
function drafts(value = input()) { return Object.fromEntries(value.lines.map(row => [row.outbound_line_id, { quantity: row.quantity, serial_ids: row.serial_ids }])) }
module.exports = { id, input, departures, history, options, preview, marker, result, drafts, sealed: returns.sealed }
