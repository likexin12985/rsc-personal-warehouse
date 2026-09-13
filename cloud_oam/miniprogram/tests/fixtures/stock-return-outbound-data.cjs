const returns = require('./stock-return-data.cjs')
const c = require('../../utils/stock-return-outbound-contract')
const { id } = returns
const at = '2026-09-13T01:00:00.123456Z', now = '2026-09-13T01:01:00Z'
function input(tracked = false) { const parent = returns.input(tracked); return { workOrderId: parent.workOrderId, personId: parent.personId,
  authorizationVersion: parent.authorizationVersion, operationId: id(20), outboundAt: at, reason: '旧件实物发往区域仓🔧\n逐件核验',
  lines: [{ operation_line_id: id(31), quantity: '1.000', serial_verifications: parent.lines[0].serial_verifications }] } }
function parent(value) { return returns.input(!!value.lines[0].serial_verifications.length) }
function options(value = input()) { const origin = returns.source(parent(value)); return { schema_version: '1.0', operation_id: value.operationId,
  operation_no: 'RET-TEST', work_order_id: value.workOrderId, person_id: value.personId, authorization_version: value.authorizationVersion,
  ledger_cursor: 10, queried_at: now, destination: returns.destination(), lines: [{ operation_line_id: id(31), source_recovery_line_id: id(11),
    source_stock_account_id: id(32), material_id: origin.material_id, sku_code: origin.sku_code, material_name: origin.material_name,
    base_unit: origin.base_unit, condition_code: origin.condition_code, lot_id: null, lot_no: null,
    tracking_mode: origin.serials.length ? 'serial' : 'none', quantity_scale: 3, allow_fraction: !origin.serials.length,
    return_quantity: '1.000', departed_quantity: '0.000', remaining_quantity: '1.000', held_quantity: '1.000', selectable_quantity: '1.000',
    serials: origin.serials.map(sn => ({ serial_id: sn.serial_id, serial_no: sn.serial_no })) }] } }
function preview(value = input()) { const choices = options(value), row = { ...choices.lines[0] }
  for (const key of ['tracking_mode', 'quantity_scale', 'allow_fraction', 'selectable_quantity', 'serials']) delete row[key]
  row.selected_quantity = value.lines[0].quantity
  row.selected_serials = value.lines[0].serial_verifications.map(sn => ({ serial_id: sn.serial_id, serial_no: sn.serial_no }))
  return { schema_version: '1.0', planning_status: 'preview_only', operation_id: value.operationId, operation_no: choices.operation_no,
    work_order_id: value.workOrderId, operator_person_id: value.personId, authorization_version: value.authorizationVersion,
    outbound_at: value.outboundAt, reason: value.reason, ledger_cursor: 10, checked_at: now, destination: choices.destination,
    request_hash: c.requestHash(value), plan_hash: 'a'.repeat(64), lines: [row] } }
function marker(value = input()) { return { v: 1, kind: c.KIND, work_order_id: value.workOrderId, person_id: value.personId,
  authorization_version: value.authorizationVersion, operation_type: c.ACTION, trace_request_id: 'wxreq-' + 'd'.repeat(36),
  request_hash: c.requestHash(value), plan_hash: 'a'.repeat(64), operation_id: value.operationId } }
function result(value = input(), request = marker(value)) { return { schema_version: '1.0', status: 'outbound', outbound_id: id(33), outbound_no: 'RET-OUT-TEST',
  operation_id: value.operationId, work_order_id: value.workOrderId, operator_person_id: value.personId, outbound_at: value.outboundAt,
  recorded_at: now, reason: value.reason, request_id: request.trace_request_id, request_hash: request.request_hash, plan_hash: request.plan_hash,
  posting_transaction_id: id(34), destination: returns.destination(), lines: preview(value).lines } }
function history(value = input(), items = [], cancelled = false) { return { schema_version: '1.0', operation_id: value.operationId,
  work_order_id: value.workOrderId, person_id: value.personId, authorization_version: value.authorizationVersion, queried_at: now,
  outbound_status: items.length ? items.reduce((n, item) => n + Number(item.lines[0].selected_quantity), 0) === 1 ? 'outbound' : 'partially_outbound' : 'not_outbound',
  original: returns.result(parent(value)), cancellation: cancelled ? returns.result(parent(value), returns.marker(parent(value), true)) : null, items } }
function drafts(value = input()) { return Object.fromEntries(value.lines.map(row => [row.operation_line_id, { quantity: row.quantity, serial_verifications: row.serial_verifications }])) }
module.exports = { id, input, options, preview, marker, result, history, drafts, sealed: returns.sealed }
